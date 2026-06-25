#include "udp_xr_source.h"

#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include <array>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstring>
#include <iostream>
#include <string>
#include <utility>

#include <nlohmann/json.hpp>

namespace teleop {
namespace {

using json = nlohmann::json;

uint64_t NowNs() {
  return static_cast<uint64_t>(
      std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::steady_clock::now().time_since_epoch())
          .count());
}

bool ReadVec(const json& v, double* out, size_t n) {
  if (!v.is_array() || v.size() != n) {
    return false;
  }
  for (size_t i = 0; i < n; ++i) {
    if (!v[i].is_number() || !std::isfinite(v[i].get<double>())) {
      return false;
    }
    out[i] = v[i].get<double>();
  }
  return true;
}

bool ParseXrCommand(const std::string& payload,
                    uint64_t receive_ns,
                    uint64_t fallback_sequence_id,
                    XRCommand* out) {
  const json root = json::parse(payload, nullptr, false);
  if (root.is_discarded() || !root.is_object()) {
    return false;
  }

  XRCommand cmd{};
  cmd.timestamp_ns = root.value("timestamp_ns", receive_ns);
  cmd.sequence_id = root.value("sequence_id", fallback_sequence_id);

  if (root.contains("position")) {
    if (!ReadVec(root["position"], cmd.right_controller_pose.p.data(), 3)) {
      return false;
    }
  }
  if (root.contains("orientation")) {
    if (!ReadVec(root["orientation"], cmd.right_controller_pose.q.data(), 4)) {
      return false;
    }
  }
  cmd.control_trigger_value = root.value("control_trigger", 0.0);
  cmd.gripper_trigger_value = root.value("gripper_trigger", 0.0);
  cmd.button_a = root.value("button_a", false);
  cmd.button_b = root.value("button_b", false);
  cmd.right_axis_click = root.value("axis_click", false);

  if (!std::isfinite(cmd.control_trigger_value) || !std::isfinite(cmd.gripper_trigger_value)) {
    return false;
  }
  *out = cmd;
  return true;
}

}  // namespace

UdpXrSource::UdpXrSource(std::string bind_ip,
                         uint16_t xr_port,
                         LatestCommandBuffer* cmd_buffer,
                         std::atomic<bool>* stop_requested)
    : bind_ip_(std::move(bind_ip)),
      xr_port_(xr_port),
      cmd_buffer_(cmd_buffer),
      stop_requested_(stop_requested) {}

UdpXrSource::~UdpXrSource() {
  Stop();
}

bool UdpXrSource::device_connected() const {
  const uint64_t last = last_packet_time_ns_.load(std::memory_order_acquire);
  if (last == 0) {
    return false;
  }
  const uint64_t now = NowNs();
  return now > last && (now - last) < 500000000ULL;  // packet within 500 ms
}

bool UdpXrSource::Start() {
  if (running_.exchange(true, std::memory_order_acq_rel)) {
    return true;
  }

  sock_ = socket(AF_INET, SOCK_DGRAM, 0);
  if (sock_ < 0) {
    running_.store(false, std::memory_order_release);
    std::cerr << "UdpXrSource: socket() failed: " << std::strerror(errno) << "\n";
    return false;
  }

  int reuse = 1;
  (void)setsockopt(sock_, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

  timeval timeout{};
  timeout.tv_sec = 0;
  timeout.tv_usec = 100000;
  (void)setsockopt(sock_, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

  sockaddr_in addr{};
  addr.sin_family = AF_INET;
  addr.sin_port = htons(xr_port_);
  if (inet_pton(AF_INET, bind_ip_.c_str(), &addr.sin_addr) != 1) {
    close(sock_);
    sock_ = -1;
    running_.store(false, std::memory_order_release);
    std::cerr << "UdpXrSource: invalid bind IP " << bind_ip_ << "\n";
    return false;
  }
  if (bind(sock_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
    close(sock_);
    sock_ = -1;
    running_.store(false, std::memory_order_release);
    std::cerr << "UdpXrSource: bind(" << bind_ip_ << ":" << xr_port_
              << ") failed: " << std::strerror(errno) << "\n";
    return false;
  }

  thread_ = std::thread(&UdpXrSource::Run, this);
  return true;
}

void UdpXrSource::Stop() {
  if (!running_.exchange(false, std::memory_order_acq_rel)) {
    return;
  }
  if (sock_ >= 0) {
    close(sock_);
    sock_ = -1;
  }
  if (thread_.joinable()) {
    thread_.join();
  }
}

void UdpXrSource::Run() {
  std::array<char, 4096> buffer{};
  while (running_.load(std::memory_order_acquire) &&
         !stop_requested_->load(std::memory_order_acquire)) {
    sockaddr_in src{};
    socklen_t src_len = sizeof(src);
    const ssize_t n = recvfrom(sock_,
                               buffer.data(),
                               buffer.size() - 1,
                               0,
                               reinterpret_cast<sockaddr*>(&src),
                               &src_len);
    if (n < 0) {
      if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR || sock_ < 0) {
        continue;
      }
      dropped_count_.fetch_add(1, std::memory_order_acq_rel);
      continue;
    }
    buffer[static_cast<size_t>(n)] = '\0';

    const uint64_t receive_ns = NowNs();
    const uint64_t next_sequence_id = sequence_id_.fetch_add(1, std::memory_order_acq_rel) + 1;
    XRCommand cmd{};
    if (!ParseXrCommand(std::string(buffer.data(), static_cast<size_t>(n)),
                        receive_ns,
                        next_sequence_id,
                        &cmd)) {
      dropped_count_.fetch_add(1, std::memory_order_acq_rel);
      continue;
    }

    cmd_buffer_->Publish(cmd);
    received_count_.fetch_add(1, std::memory_order_acq_rel);
    last_packet_time_ns_.store(receive_ns, std::memory_order_release);
  }
}

}  // namespace teleop

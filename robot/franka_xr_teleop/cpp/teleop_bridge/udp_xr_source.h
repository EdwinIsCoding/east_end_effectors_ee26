#pragma once

#include <atomic>
#include <cstdint>
#include <string>
#include <thread>

#include "common_types.h"

namespace teleop {

// Feeds the SAME XRCommand buffer the Quest source fills, but from UDP JSON packets instead of the
// XRoboToolkit SDK. This lets a desktop keyboard/mouse client drive the bridge while reusing the
// exact teleop mapper + IK + safety path (control_source stays kXr). Mirror of PolicyActionSource,
// but publishes XRCommand (controller pose + triggers + buttons) rather than a joint action.
//
// Wire format (JSON, one datagram per command):
//   {"timestamp_ns":<u64?>, "sequence_id":<u64?>,
//    "position":[x,y,z], "orientation":[qx,qy,qz,qw],
//    "control_trigger":0..1, "gripper_trigger":0..1,
//    "button_a":bool, "button_b":bool, "axis_click":bool}
// timestamp_ns/sequence_id are optional; the source stamps/auto-increments when absent.
class UdpXrSource {
 public:
  UdpXrSource(std::string bind_ip,
              uint16_t xr_port,
              LatestCommandBuffer* cmd_buffer,
              std::atomic<bool>* stop_requested);
  ~UdpXrSource();

  bool Start();
  void Stop();

  // Same accessor surface as XrRoboticsSource so main.cpp can treat them alike.
  uint64_t last_packet_time_ns() const { return last_packet_time_ns_.load(std::memory_order_acquire); }
  uint64_t received_count() const { return received_count_.load(std::memory_order_acquire); }
  uint64_t dropped_count() const { return dropped_count_.load(std::memory_order_acquire); }
  bool server_connected() const { return running_.load(std::memory_order_acquire); }
  bool device_connected() const;  // true if a packet arrived recently

 private:
  void Run();

  std::string bind_ip_;
  uint16_t xr_port_ = 0;
  LatestCommandBuffer* cmd_buffer_;
  std::atomic<bool>* stop_requested_;

  std::atomic<bool> running_{false};
  std::thread thread_;
  int sock_ = -1;

  std::atomic<uint64_t> sequence_id_{0};
  std::atomic<uint64_t> last_packet_time_ns_{0};
  std::atomic<uint64_t> received_count_{0};
  std::atomic<uint64_t> dropped_count_{0};
};

}  // namespace teleop

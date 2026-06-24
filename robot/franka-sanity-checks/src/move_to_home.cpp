// Minimal, conservative point-to-point joint move to the EE26 default "home" pose.
// Smooth quintic time-scaling (zero vel/accel at both ends) over a fixed duration.
// Home pose matches the teleop bridge startup home in common_types.h / teleop.yaml.
//
// Build (libfranka 0.9.2 local prefix):
//   g++ -std=c++17 src/move_to_home.cpp -o build/move_to_home \
//     -I$HOME/opt/libfranka-0.9.2/include -I/usr/include/eigen3 \
//     -L$HOME/opt/libfranka-0.9.2/lib -lfranka -lpthread \
//     -Wl,-rpath,$HOME/opt/libfranka-0.9.2/lib
//
// Usage: ./build/move_to_home <robot-ip> [duration_s=6.0]

#include <array>
#include <cmath>
#include <iostream>
#include <string>

#include <franka/exception.h>
#include <franka/robot.h>

namespace {
constexpr std::array<double, 7> kHome = {
    {0.0, -0.7853981633974483, 0.0, -2.356194490192345, 0.0, 1.5707963267948966,
     0.7853981633974483}};

// Franka soft joint limits (rad) for a Panda, used only for a sanity clamp/abort.
constexpr std::array<double, 7> kQMin = {
    {-2.8973, -1.7628, -2.8973, -3.0718, -2.8973, -0.0175, -2.8973}};
constexpr std::array<double, 7> kQMax = {
    {2.8973, 1.7628, 2.8973, -0.0698, 2.8973, 3.7525, 2.8973}};

double Quintic(double tau) {  // s(0)=0, s(1)=1, s'=s''=0 at ends
  if (tau <= 0.0) return 0.0;
  if (tau >= 1.0) return 1.0;
  return 10.0 * tau * tau * tau - 15.0 * tau * tau * tau * tau +
         6.0 * tau * tau * tau * tau * tau;
}
}  // namespace

int main(int argc, char** argv) {
  if (argc < 2) {
    std::cerr << "Usage: " << argv[0] << " <robot-ip> [duration_s=6.0]\n";
    return 1;
  }
  const std::string robot_ip = argv[1];
  const double duration_s = (argc >= 3) ? std::stod(argv[2]) : 6.0;
  if (duration_s < 2.0) {
    std::cerr << "Refusing duration < 2 s (too fast for a supervised home move).\n";
    return 1;
  }

  try {
    franka::Robot robot(robot_ip);
    std::cout << "Connected to robot at " << robot_ip
              << " (server v" << robot.serverVersion() << ")\n";

    // Match the production teleop bridge's safety envelope exactly
    // (franka_controller.cpp ConfigureConservativeBehavior + robot.yaml load):
    // collision thresholds (lower/upper, acceleration + nominal), joint impedance,
    // and external load.
    robot.setCollisionBehavior(
        {{20.0, 20.0, 18.0, 18.0, 16.0, 14.0, 12.0}},   // lower torque, accel
        {{20.0, 20.0, 18.0, 18.0, 16.0, 14.0, 12.0}},   // upper torque, accel
        {{20.0, 20.0, 18.0, 18.0, 16.0, 14.0, 12.0}},   // lower torque, nominal
        {{20.0, 20.0, 18.0, 18.0, 16.0, 14.0, 12.0}},   // upper torque, nominal
        {{20.0, 20.0, 20.0, 25.0, 25.0, 25.0}},         // lower force, accel
        {{20.0, 20.0, 20.0, 25.0, 25.0, 25.0}},         // upper force, accel
        {{20.0, 20.0, 20.0, 25.0, 25.0, 25.0}},         // lower force, nominal
        {{20.0, 20.0, 20.0, 25.0, 25.0, 25.0}});        // upper force, nominal
    robot.setJointImpedance({{1500.0, 1500.0, 1500.0, 1250.0, 1250.0, 1000.0, 1000.0}});
    robot.setLoad(0.0, {{0.0, 0.0, 0.0}},
                  {{0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0}});  // robot.yaml load

    const franka::RobotState initial = robot.readOnce();
    const std::array<double, 7> q_start = initial.q;

    std::array<double, 7> q_goal = kHome;
    double max_delta = 0.0;
    for (size_t i = 0; i < 7; ++i) {
      if (q_goal[i] < kQMin[i] || q_goal[i] > kQMax[i]) {
        std::cerr << "Goal joint " << i << " out of limits. Abort.\n";
        return 2;
      }
      max_delta = std::max(max_delta, std::abs(q_goal[i] - q_start[i]));
    }

    std::cout << "q_start = [";
    for (size_t i = 0; i < 7; ++i) std::cout << q_start[i] << (i < 6 ? ", " : "]\n");
    std::cout << "q_goal  = [";
    for (size_t i = 0; i < 7; ++i) std::cout << q_goal[i] << (i < 6 ? ", " : "]\n");
    // Safety is governed by peak joint speed, not total distance: a large move is
    // fine if slow. Sanity backstop only — a single joint's full range is ~2.9 rad,
    // and the goal is already clamped to joint limits above.
    constexpr double kMaxRehomeDelta = 3.0;   // rad
    // Match the bridge's rehome joint speed (franka_controller.cpp
    // kRehomeJointSpeedRadPerS = 0.35). Quintic peak = 1.875*delta/T.
    constexpr double kPeakSpeedCap = 0.35;    // rad/s
    if (max_delta > kMaxRehomeDelta) {
      std::cerr << "max_delta " << max_delta << " > " << kMaxRehomeDelta
                << " rad — unexpectedly large, aborting for safety.\n";
      return 2;
    }
    // Extend the requested duration if needed to keep the peak speed under the cap.
    const double eff_duration =
        std::max(duration_s, 1.875 * max_delta / kPeakSpeedCap);
    std::cout << "max joint delta = " << max_delta << " rad; using " << eff_duration
              << " s (peak speed ~" << (1.875 * max_delta / eff_duration) << " rad/s)\n";

    double time = 0.0;
    robot.control([&](const franka::RobotState&,
                      franka::Duration period) -> franka::JointPositions {
      time += period.toSec();
      const double tau = time / eff_duration;
      const double s = Quintic(tau);
      std::array<double, 7> q_cmd{};
      for (size_t i = 0; i < 7; ++i) {
        q_cmd[i] = q_start[i] + s * (q_goal[i] - q_start[i]);
      }
      franka::JointPositions out(q_cmd);
      if (tau >= 1.0) {
        return franka::MotionFinished(out);
      }
      return out;
    },
    franka::ControllerMode::kJointImpedance,
    /*limit_rate=*/true,        // robot.yaml limit_rate
    /*cutoff_frequency=*/100.0  // robot.yaml lpf_cutoff_frequency
    );

    const franka::RobotState final_state = robot.readOnce();
    std::cout << "q_final = [";
    for (size_t i = 0; i < 7; ++i)
      std::cout << final_state.q[i] << (i < 6 ? ", " : "]\n");
    std::cout << "Move-to-home completed successfully.\n";
    return 0;
  } catch (const franka::Exception& e) {
    std::cerr << "franka::Exception: " << e.what() << "\n";
    return 3;
  }
}

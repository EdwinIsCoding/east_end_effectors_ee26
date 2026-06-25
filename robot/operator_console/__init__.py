"""EE26 operator console — live camera feeds + robot telemetry dashboard.

Consumes the teleop bridge's UDP observation stream (port 28081, CONTRACT §3) and the two D405
cameras, and serves a single-page web dashboard. Read-only: it never commands the robot.
"""

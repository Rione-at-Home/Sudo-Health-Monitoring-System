#!/usr/bin/env python3
"""
robot_health_coordinator.py

Robot-wide hardware health aggregation node. Hardware/Control owned.

This is a FOUNDATION, not a finished implementation. It exists to fix the
shape of the system so Hardware and NLP can build against a stable
interface while the individual subsystem health sources (especially the
head and base) are still being developed.

Responsibility
    Aggregate independent, subsystem-level health summaries (arm, head,
    base, ...) into a single robot-level health result, and publish that
    result for other nodes -- chiefly `task_coordinator` -- to consume.

Explicitly NOT this node's job (see README "boundaries this project
preserves"):
    * opening a serial port
    * reading or writing Dynamixel (or any) hardware registers directly
    * publishing motor commands
    * TTS / conversational behavior
    * LLM / natural-language logic

Subsystem sources today
    ARM:  arm_health_coordinator (existing, nova_arm_ws) already diagnoses
          the arm from ArmDriver's /arm_servo_status and publishes a
          summary. This node subscribes to that summary rather than
          re-reading raw servo diagnostics or re-implementing arm fault
          logic -- see README, "Existing arm health interface".
    HEAD: no health interface exists yet. HeadNode currently has no status
          publisher. `head_status_topic` is a placeholder; until HeadNode
          (or a companion node) publishes on it, the head subsystem stays
          UNKNOWN. See README, "HeadNode: health interface does not exist
          yet".
    BASE: same situation as HEAD, for KobukiNode / the mobile base.

Publishes
    /robot_health_status  std_msgs/String  JSON, latched, re-sent at
                          `publish_rate_hz`. See `build_status_payload()`
                          for the schema.
    /robot_health_event   std_msgs/String  coarse event names, emitted only
                          on a change to the *overall* robot state
                          (e.g. ROBOT_DEGRADED, ROBOT_HEALTHY).
Subscribes
    /arm_health_state    std_msgs/String                (nova_arm_ws, exists)
    /arm_health_detail   std_msgs/String (JSON)          (nova_arm_ws, exists)
    <head_status_topic>  diagnostic_msgs/DiagnosticArray (TODO: HeadNode)
    <base_status_topic>  diagnostic_msgs/DiagnosticArray (TODO: KobukiNode)

Robot-level states (per subsystem, and for the overall result)
    HEALTHY     subsystem is reporting and nothing is wrong
    DEGRADED    subsystem is reporting a non-critical problem
    FAULT       subsystem is reporting a problem it cannot recover from
                automatically, or has given up trying
    RECOVERING  subsystem is actively attempting an automated fix
    UNKNOWN     no (recent) status has been received for this subsystem.
                This is the default for any subsystem whose driver / health
                node is not running -- it must never be confused with FAULT.
"""

import json
from dataclasses import dataclass, field
from typing import Optional

import rclpy
from diagnostic_msgs.msg import DiagnosticArray
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

# ----------------------------------------------------------------------
# Robot-level vocabulary. Subsystem-specific detail (e.g. arm_health_
# coordinator's DIAGNOSTIC/VERIFYING sub-states) is preserved in each
# subsystem's `detail` field, but everything reported at the robot level
# is normalized to one of these five.
# ----------------------------------------------------------------------
HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
FAULT = "FAULT"
RECOVERING = "RECOVERING"
UNKNOWN = "UNKNOWN"

ALL_STATES = (HEALTHY, DEGRADED, RECOVERING, FAULT, UNKNOWN)

# Worst-first ordering used to fold multiple subsystems into one overall
# state. FAULT is worse than RECOVERING is worse than DEGRADED, etc.
# TODO(hardware): revisit once we know how FAULT/DEGRADED on one subsystem
# should actually affect the robot as a whole (e.g. does a HEAD fault mean
# the robot is not "okay" the same way an ARM fault does?).
_SEVERITY = {FAULT: 4, RECOVERING: 3, DEGRADED: 2, UNKNOWN: 1, HEALTHY: 0}


@dataclass
class SubsystemHealth:
    """Everything the coordinator knows about one subsystem."""

    name: str
    timeout_s: float
    state: str = UNKNOWN
    detail: str = "no status received yet"
    last_update: Optional[float] = None   # node clock seconds, None = never
    raw: dict = field(default_factory=dict)  # subsystem-specific extra info

    def touch(self, now: float, state: str, detail: str, raw: Optional[dict] = None):
        self.last_update = now
        self.state = state
        self.detail = detail
        if raw is not None:
            self.raw = raw

    def apply_timeout(self, now: float):
        """If we haven't heard from this subsystem recently, it's UNKNOWN.

        This is what keeps a subsystem whose driver isn't running from
        looking like anything other than "no data" -- see the module
        docstring and the README section on missing drivers.
        """
        if self.last_update is None or (now - self.last_update) > self.timeout_s:
            if self.state != UNKNOWN:
                self.state = UNKNOWN
                self.detail = "no status received within timeout"

    def to_dict(self):
        return {"state": self.state, "detail": self.detail, **self.raw}


class RobotHealthCoordinator(Node):

    def __init__(self):
        super().__init__("robot_health_coordinator")

        d = self.declare_parameter
        # Existing arm interface (nova_arm_ws / arm_health_coordinator).
        d("arm_state_topic", "/arm_health_state")
        d("arm_detail_topic", "/arm_health_detail")
        d("arm_timeout_s", 5.0)

        # Placeholders: no publisher exists yet. See README TODOs.
        d("head_status_topic", "/head_health_status")
        d("head_timeout_s", 5.0)
        d("base_status_topic", "/base_health_status")
        d("base_timeout_s", 5.0)

        d("publish_rate_hz", 1.0)

        g = lambda n: self.get_parameter(n).value  # noqa: E731
        self.publish_rate_hz = float(g("publish_rate_hz"))

        # ---- subsystem registry -----------------------------------------
        # TODO(hardware): when the dual-arm ArmDriver lands, this becomes
        # two entries ("arm_left", "arm_right") instead of one "arm" --
        # see README, "Arms: temporary single-arm, target dual-arm". The
        # rest of this node does not assume anything about how many
        # entries are in `self.subsystems`, specifically so that change is
        # additive.
        self.subsystems = {
            "arm": SubsystemHealth("arm", timeout_s=float(g("arm_timeout_s"))),
            "head": SubsystemHealth("head", timeout_s=float(g("head_timeout_s"))),
            "base": SubsystemHealth("base", timeout_s=float(g("base_timeout_s"))),
        }

        # ---- publishers ----------------------------------------------------
        latched = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self.status_pub = self.create_publisher(String, "/robot_health_status", latched)
        self.event_pub = self.create_publisher(String, "/robot_health_event", 10)

        # ---- subscriptions --------------------------------------------------
        self.create_subscription(
            String, g("arm_state_topic"), self._arm_state_cb, 10
        )
        self.create_subscription(
            String, g("arm_detail_topic"), self._arm_detail_cb, 10
        )

        # TODO(hardware): DiagnosticArray is this node's best guess for what
        # HeadNode / KobukiNode will eventually publish, matching the arm's
        # /arm_servo_status pattern. Update the message type here (and in
        # _evaluate_head / _evaluate_base below) once that's settled -- see
        # README "Expected ROS2 interfaces".
        self.create_subscription(
            DiagnosticArray, g("head_status_topic"), self._head_status_cb, 10
        )
        self.create_subscription(
            DiagnosticArray, g("base_status_topic"), self._base_status_cb, 10
        )

        self._last_arm_state_msg: Optional[str] = None
        self._last_arm_detail_msg: Optional[dict] = None
        self._overall_state = UNKNOWN

        self.create_timer(1.0 / max(self.publish_rate_hz, 0.1), self._tick)

        self.get_logger().info(
            "robot_health_coordinator up. arm <- %s / %s, head <- %s (TODO: "
            "not yet published by HeadNode), base <- %s (TODO: not yet "
            "published by KobukiNode)."
            % (g("arm_state_topic"), g("arm_detail_topic"),
               g("head_status_topic"), g("base_status_topic"))
        )

    def now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    # ------------------------------------------------------------ ARM -----
    # arm_health_coordinator already does the hard work (servo-level
    # diagnosis, recovery). This node just re-expresses its summary in
    # robot-level vocabulary; it never touches /arm_servo_status directly.

    _ARM_STATE_MAP = {
        "HEALTHY": HEALTHY,
        "DIAGNOSTIC": DEGRADED,
        "RECOVERING": RECOVERING,
        "VERIFYING": RECOVERING,
        "FAULT": FAULT,
    }

    def _arm_state_cb(self, msg: String):
        self._last_arm_state_msg = msg.data
        self._evaluate_arm()

    def _arm_detail_cb(self, msg: String):
        try:
            self._last_arm_detail_msg = json.loads(msg.data)
        except (json.JSONDecodeError, TypeError):
            self._last_arm_detail_msg = None
        self._evaluate_arm()

    def _evaluate_arm(self):
        """Map arm_health_coordinator's state name onto robot-level state.

        Kept intentionally simple: arm_health_coordinator has already done
        the diagnosis, so there is no fault-detection logic to duplicate
        here.
        """
        if self._last_arm_state_msg is None:
            return
        state = self._ARM_STATE_MAP.get(self._last_arm_state_msg, UNKNOWN)
        reason = None
        if isinstance(self._last_arm_detail_msg, dict):
            reason = self._last_arm_detail_msg.get("reason")
        detail = reason or self._last_arm_state_msg
        raw = {"upstream_state": self._last_arm_state_msg}
        if self._last_arm_detail_msg is not None:
            raw["upstream_detail"] = self._last_arm_detail_msg
        self.subsystems["arm"].touch(self.now(), state, detail, raw)

    # ----------------------------------------------------------- HEAD -----

    def _head_status_cb(self, msg: DiagnosticArray):
        self._evaluate_head(msg)

    def _evaluate_head(self, msg: DiagnosticArray):
        """TODO(hardware): real head fault evaluation.

        This is a placeholder that only proves the wiring works once
        HeadNode starts publishing. Once the real message shape is known,
        replace this with logic analogous to arm_health_coordinator's
        `evaluate()` (e.g. per-motor communication loss, overheating,
        overload -- whatever is meaningful for the AX-12A pan/tilt motors
        and the XM430 cat-head motors).
        """
        levels = [status.level for status in msg.status]
        if any(level >= 2 for level in levels):        # diagnostic ERROR
            state, detail = FAULT, "one or more head motors report ERROR"
        elif any(level == 1 for level in levels):       # diagnostic WARN
            state, detail = DEGRADED, "one or more head motors report WARN"
        else:
            state, detail = HEALTHY, "head status nominal"
        self.subsystems["head"].touch(self.now(), state, detail)

    # ----------------------------------------------------------- BASE -----

    def _base_status_cb(self, msg: DiagnosticArray):
        self._evaluate_base(msg)

    def _evaluate_base(self, msg: DiagnosticArray):
        """TODO(hardware): real base fault evaluation, once KobukiNode

        publishes something. Placeholder mirrors `_evaluate_head` for now;
        replace with whatever is meaningful for the mobile base (e.g.
        battery state, bumper/cliff sensors, wheel drop, communication
        loss).
        """
        levels = [status.level for status in msg.status]
        if any(level >= 2 for level in levels):
            state, detail = FAULT, "one or more base diagnostics report ERROR"
        elif any(level == 1 for level in levels):
            state, detail = DEGRADED, "one or more base diagnostics report WARN"
        else:
            state, detail = HEALTHY, "base status nominal"
        self.subsystems["base"].touch(self.now(), state, detail)

    # ------------------------------------------------------- aggregation --

    def _tick(self):
        now = self.now()
        for sub in self.subsystems.values():
            sub.apply_timeout(now)

        overall = self._compute_overall()
        if overall != self._overall_state:
            self.get_logger().info(
                f"robot health {self._overall_state} -> {overall}"
            )
            self.event_pub.publish(String(data=f"ROBOT_{overall}"))
            self._overall_state = overall

        self.status_pub.publish(String(data=self._build_status_payload(overall)))

    def _compute_overall(self) -> str:
        """Worst-of-all-subsystems. See TODO on `_SEVERITY` above.

        Deliberately simple for this foundation: a single UNKNOWN
        subsystem (e.g. base not running yet during bench testing) does
        not by itself make the overall state worse than the worst *known*
        subsystem, unless everything is UNKNOWN. Hardware/NLP should
        revisit whether that's the right robot-level semantics as more
        subsystems come online -- see README "Robot-wide state semantics"
        under shared responsibilities.
        """
        states = [s.state for s in self.subsystems.values()]
        known = [s for s in states if s != UNKNOWN]
        if not known:
            return UNKNOWN
        return max(known, key=lambda s: _SEVERITY[s])

    def _build_status_payload(self, overall: str) -> str:
        payload = {
            "overall": overall,
            "subsystems": {
                name: sub.to_dict() for name, sub in self.subsystems.items()
            },
        }
        return json.dumps(payload)

    # ------------------------------------------------- future recovery ----
    # TODO(hardware): robot-level recovery orchestration hook.
    #
    # arm_health_coordinator already runs its own recovery loop for the
    # arm. This method is a placeholder for anything that genuinely needs
    # to be decided at the robot level instead -- for example, sequencing
    # recovery across subsystems, or deciding the robot should stop
    # accepting task_coordinator requests while a subsystem is
    # RECOVERING. It is not called anywhere yet.
    def _attempt_robot_level_recovery(self):
        raise NotImplementedError(
            "robot-level recovery orchestration is not yet defined; see "
            "TODO in robot_health_coordinator.py"
        )


def main(args=None):
    rclpy.init(args=args)
    node = RobotHealthCoordinator()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
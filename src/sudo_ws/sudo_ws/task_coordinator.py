#!/usr/bin/env python3
"""
task_coordinator.py

High-level task/interaction orchestration node. NLP owned (interface to
robot_health_coordinator is shared with Hardware).

This is a FOUNDATION, not a finished implementation. It wires together the
places a real STT/LLM/TTS stack will plug into, and demonstrates the
one piece of behavior the architecture requires today: answering a health
question using `robot_health_coordinator`'s published state, without
touching hardware or re-implementing any diagnosis logic.

Responsibility
    Sit between (STT -> LLM) and the robot's approved high-level
    capabilities -- today just `robot_health_coordinator` -- and route
    requests between them. Owns TTS output.

Explicitly NOT this node's job (see README "boundaries this project
preserves"):
    * touching hardware registers or Dynamixel motors directly
    * re-implementing robot_health_coordinator's (or arm_health_
      coordinator's) diagnosis logic -- this node only *reads* published
      health state and talks about it
    * being an actual LLM -- `_handle_user_text()` below is a placeholder

Subscribes
    /robot_health_status  std_msgs/String (JSON)  robot_health_coordinator's
                          latest aggregated state (see that node's
                          `build_status_payload` schema).
    <stt_topic>           std_msgs/String          TODO: not wired to a real
                          STT engine yet. Placeholder so the message flow
                          exists end-to-end; publish plain text here to
                          exercise `_handle_user_text()` by hand.
Publishes
    <tts_topic>           std_msgs/String          TODO: not wired to a real
                          TTS engine yet. `_speak()` is the single choke
                          point every response should go through.
"""

import json
from typing import Callable, Optional

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class TaskCoordinator(Node):

    def __init__(self):
        super().__init__("task_coordinator")

        d = self.declare_parameter
        d("robot_health_topic", "/robot_health_status")
        # TODO(nlp): point these at whatever the real STT/TTS nodes end up
        # using. Placeholder topic names so the wiring exists now.
        d("stt_topic", "/stt/transcript")
        d("tts_topic", "/tts/say")

        g = lambda n: self.get_parameter(n).value  # noqa: E731

        # ---- state ------------------------------------------------------
        # Latest robot_health_coordinator payload, or None if we haven't
        # heard from it yet. Never guess at health if this is None --
        # say so instead (mirrors robot_health_coordinator's own UNKNOWN
        # handling for a missing subsystem).
        self._robot_health: Optional[dict] = None

        # ---- pub/sub ------------------------------------------------------
        self.create_subscription(
            String, g("robot_health_topic"), self._robot_health_cb, 10
        )

        # TODO(nlp): replace with the real STT node's output topic/type.
        self.create_subscription(
            String, g("stt_topic"), self._stt_cb, 10
        )

        # TODO(nlp): replace with the real TTS node's input topic/type.
        self.tts_pub = self.create_publisher(String, g("tts_topic"), 10)

        # ---- capability / "tool" registry --------------------------------
        # TODO(nlp): this is the seam where real LLM tool/function calling
        # attaches. Each entry should become a proper tool schema (name,
        # description, JSON args) for whatever LLM function-calling
        # interface is chosen. For now it's just a name -> callable map so
        # the routing logic below has something real to call, and so it is
        # obvious where a new "approved robot interface" gets registered.
        self.capabilities: dict[str, Callable[[], dict]] = {
            "get_robot_health": self.get_robot_health,
        }

        self.get_logger().info(
            "task_coordinator up. Listening for robot health on %s, "
            "placeholder STT input on %s (TODO: not wired to real STT), "
            "placeholder TTS output on %s (TODO: not wired to real TTS)."
            % (g("robot_health_topic"), g("stt_topic"), g("tts_topic"))
        )

    # ------------------------------------------------------- health input --

    def _robot_health_cb(self, msg: String):
        try:
            self._robot_health = json.loads(msg.data)
        except (json.JSONDecodeError, TypeError):
            self.get_logger().warn("Could not parse /robot_health_status payload")

    def get_robot_health(self) -> dict:
        """The one capability this foundation actually implements.

        Returns robot_health_coordinator's last known payload verbatim
        (or an explicit "no data yet" marker). This is the function an
        LLM tool-calling layer should call for any "are you okay?" /
        "what's wrong with you?" style question -- see README's example
        interaction.
        """
        if self._robot_health is None:
            return {"overall": "UNKNOWN", "subsystems": {},
                    "note": "no data received yet from robot_health_coordinator"}
        return self._robot_health

    # ------------------------------------------------------------- STT ----

    def _stt_cb(self, msg: String):
        """TODO(nlp): this currently receives whatever is published on the
        placeholder STT topic and treats it as final transcribed text.
        Wire in the real STT engine's output here.
        """
        self._handle_user_text(msg.data)

    # -------------------------------------------------- request handling --

    def _handle_user_text(self, text: str):
        """TODO(nlp): replace this with real LLM tool-calling.

        This placeholder exists only to prove the architecture end-to-end:
        a human utterance comes in, gets routed to an approved capability
        (never straight to hardware), and the result goes out through
        `_speak()`. It intentionally does the bare minimum -- simple
        keyword matching -- rather than trying to approximate an LLM.
        """
        lowered = text.lower()
        health_keywords = ("okay", "ok?", "alright", "working", "wrong",
                            "status", "health", "fixed")

        if any(kw in lowered for kw in health_keywords):
            health = self.capabilities["get_robot_health"]()
            self._speak(self._describe_health(health))
            return

        self.get_logger().info(f"No placeholder handler for: {text!r}")
        self._speak(
            "I don't have a real language model wired up yet, so I can "
            "only answer basic health questions right now."
        )

    def _describe_health(self, health: dict) -> str:
        """TODO(nlp): this is the wording an LLM should eventually own.

        Kept as plain string formatting for now so the placeholder path in
        `_handle_user_text` has something concrete to say -- see README's
        example interaction for the target tone.
        """
        overall = health.get("overall", "UNKNOWN")
        subsystems = health.get("subsystems", {})

        if overall == "UNKNOWN" and not subsystems:
            return "I don't have a health report from my systems yet."

        healthy = [n for n, s in subsystems.items() if s.get("state") == "HEALTHY"]
        not_healthy = {
            n: s.get("state") for n, s in subsystems.items()
            if s.get("state") not in ("HEALTHY",)
        }

        parts = []
        if healthy:
            parts.append(f"{', '.join(healthy)} healthy")
        for name, state in not_healthy.items():
            if state == "UNKNOWN":
                parts.append(f"no status report from {name}")
            else:
                parts.append(f"{name} is {state.lower()}")

        return "; ".join(parts) if parts else f"Overall status: {overall}."

    # --------------------------------------------------------------- TTS --

    def _speak(self, text: str):
        """Single choke point for anything this node says out loud.

        TODO(nlp): currently just republishes on the placeholder TTS
        topic. Route through the real TTS node's interface once it
        exists.
        """
        self.get_logger().info(f"[TTS placeholder] {text}")
        self.tts_pub.publish(String(data=text))

    # --------------------------------------------------- future routing ---
    # TODO(nlp + hardware): as more "approved robot interfaces" come
    # online (Presenter, navigation, other task coordinators mentioned in
    # the README's architecture diagram), register them in
    # `self.capabilities` here rather than special-casing them in
    # `_handle_user_text`. This keeps the set of things the LLM is allowed
    # to call explicit and centralized in one place.


def main(args=None):
    rclpy.init(args=args)
    node = TaskCoordinator()
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
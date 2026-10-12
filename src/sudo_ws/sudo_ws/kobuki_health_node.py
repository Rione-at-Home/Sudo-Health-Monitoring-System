import time

import rclpy

from rclpy.node import Node

from diagnostic_msgs.msg import (
    DiagnosticArray,
    DiagnosticStatus,
    KeyValue
)

from .KobukiDriver import KobukiDriver


class KobukiNode(Node):

    def __init__(self):

        super().__init__("kobuki_node")

        self.declare_parameter(
            "device",
            "/dev/kobuki"
        )

        device = (
            self.get_parameter("device")
            .get_parameter_value()
            .string_value
        )

        self.driver = KobukiDriver(
            device=device
        )

        self.health_pub = self.create_publisher(
            DiagnosticArray,
            "/base_health_status",
            10
        )

        self.last_basic = None
        self.last_current = None

        self.last_fault_state = None

        self.read_timer = self.create_timer(
            0.02,
            self.read_kobuki
        )

        self.health_timer = self.create_timer(
            5.0,
            self.publish_health
        )

        self.get_logger().info(
            f"Kobuki connected on {device}"
        )

    def read_kobuki(self):

        packets = self.driver.poll()

        for packet in packets:

            if "basic" in packet:
                if packet["basic"] is not None:
                    self.last_basic = packet["basic"]

            if "current" in packet:
                if packet["current"] is not None:
                    self.last_current = packet["current"]

            self.check_immediate_fault()

    def check_immediate_fault(self):

        if self.last_basic is None:
            return

        overcurrent = (
            self.last_basic["overcurrent"]
        )

        wheel_drop = (
            self.last_basic["wheel_drop"]
        )

        fault_state = (
            overcurrent,
            wheel_drop
        )

        if fault_state == self.last_fault_state:
            return

        self.last_fault_state = fault_state

        if overcurrent != 0:
            self.publish_health()

        elif wheel_drop != 0:
            self.publish_health()

    def publish_health(self):

        if self.last_basic is None:
            return

        msg = DiagnosticArray()

        msg.header.stamp = (
            self.get_clock()
            .now()
            .to_msg()
        )

        statuses = []

        communication = DiagnosticStatus()

        communication.name = (
            "base/communication"
        )

        communication.hardware_id = (
            "kobuki"
        )

        if self.driver.last_rx_time is None:

            communication.level = (
                DiagnosticStatus.ERROR
            )

            communication.message = (
                "No Kobuki data received"
            )

        else:

            age = (
                time.monotonic()
                - self.driver.last_rx_time
            )

            if age > 1.0:

                communication.level = (
                    DiagnosticStatus.ERROR
                )

                communication.message = (
                    "Kobuki communication lost"
                )

            else:

                communication.level = (
                    DiagnosticStatus.OK
                )

                communication.message = (
                    "Kobuki communication healthy"
                )

        statuses.append(communication)

        wheel_status = DiagnosticStatus()

        wheel_status.name = "base/wheels"
        wheel_status.hardware_id = "kobuki"

        overcurrent = (
            self.last_basic["overcurrent"]
        )

        wheel_drop = (
            self.last_basic["wheel_drop"]
        )

        if overcurrent != 0:

            wheel_status.level = (
                DiagnosticStatus.ERROR
            )

            wheel_status.message = (
                "Wheel motor overcurrent detected"
            )

        elif wheel_drop != 0:

            wheel_status.level = (
                DiagnosticStatus.WARN
            )

            wheel_status.message = (
                "Wheel drop detected"
            )

        else:

            wheel_status.level = (
                DiagnosticStatus.OK
            )

            wheel_status.message = (
                "Wheel system healthy"
            )

        wheel_status.values = [
            KeyValue(
                key="overcurrent",
                value=str(overcurrent)
            ),
            KeyValue(
                key="wheel_drop",
                value=str(wheel_drop)
            ),
            KeyValue(
                key="left_encoder",
                value=str(
                    self.last_basic[
                        "left_encoder"
                    ]
                )
            ),
            KeyValue(
                key="right_encoder",
                value=str(
                    self.last_basic[
                        "right_encoder"
                    ]
                )
            )
        ]

        statuses.append(wheel_status)

        battery = DiagnosticStatus()

        battery.name = "base/battery"
        battery.hardware_id = "kobuki"

        battery.level = DiagnosticStatus.OK

        battery.message = (
            "Battery telemetry available"
        )

        battery.values = [
            KeyValue(
                key="voltage",
                value=str(
                    self.last_basic[
                        "battery_voltage"
                    ]
                )
            ),
            KeyValue(
                key="charger",
                value=str(
                    self.last_basic[
                        "charger"
                    ]
                )
            )
        ]

        statuses.append(battery)

        msg.status = statuses

        self.health_pub.publish(msg)

    def destroy_node(self):

        try:
            self.driver.close()

        finally:
            super().destroy_node()


def main(args=None):

    rclpy.init(args=args)

    node = KobukiNode()

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

import serial
import time


class KobukiDriver:

    HEADER = b"\xAA\x55"

    def __init__(self, device="/dev/kobuki", baudrate=115200):
        self.serial = serial.Serial(
            device,
            baudrate=baudrate,
            timeout=0
        )

        self.buffer = bytearray()
        self.last_rx_time = None

    def poll(self):
        waiting = self.serial.in_waiting

        if waiting > 0:
            self.buffer.extend(
                self.serial.read(waiting)
            )

        packets = []

        while True:
            header_index = self.buffer.find(self.HEADER)

            if header_index == -1:
                if self.buffer[-1:] == b"\xAA":
                    self.buffer[:] = b"\xAA"
                else:
                    self.buffer.clear()

                break

            if header_index > 0:
                del self.buffer[:header_index]

            if len(self.buffer) < 4:
                break

            payload_length = self.buffer[2]
            packet_length = payload_length + 4

            if len(self.buffer) < packet_length:
                break

            frame = bytes(
                self.buffer[:packet_length]
            )

            del self.buffer[:packet_length]

            checksum = 0

            for byte in frame[2:]:
                checksum ^= byte

            if checksum != 0:
                continue

            payload = frame[
                3:3 + payload_length
            ]

            self.last_rx_time = time.monotonic()

            packets.append(
                self.parse_payload(payload)
            )

        return packets

    def parse_payload(self, payload):
        result = {}

        index = 0

        while index + 2 <= len(payload):

            packet_id = payload[index]
            packet_length = payload[index + 1]

            start = index + 2
            end = start + packet_length

            if end > len(payload):
                break

            data = payload[start:end]

            if packet_id == 1:
                result["basic"] = (
                    self.parse_basic_sensor(data)
                )

            elif packet_id == 6:
                result["current"] = (
                    self.parse_motor_current(data)
                )

            index = end

        return result

    def parse_basic_sensor(self, data):

        if len(data) < 15:
            return None

        return {
            "timestamp":
                int.from_bytes(data[0:2], "little"),

            "bumper":
                data[2],

            "wheel_drop":
                data[3],

            "cliff":
                data[4],

            "left_encoder":
                int.from_bytes(data[5:7], "little"),

            "right_encoder":
                int.from_bytes(data[7:9], "little"),

            "left_pwm":
                self.to_signed_byte(data[9]),

            "right_pwm":
                self.to_signed_byte(data[10]),

            "button":
                data[11],

            "charger":
                data[12],

            "battery_voltage":
                data[13] * 0.1,

            "overcurrent":
                data[14]
        }

    def parse_motor_current(self, data):

        if len(data) < 2:
            return None

        return {
            "left_current_a":
                data[0] * 0.01,

            "right_current_a":
                data[1] * 0.01
        }

    def to_signed_byte(self, value):

        if value > 127:
            return value - 256

        return value

    def close(self):

        if self.serial.is_open:
            self.serial.close()

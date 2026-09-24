# Sudo-Health-Monitoring-System

## Overview

This project is a continuation and expansion of the health monitoring and recovery system originally developed for the Nova Arm in [`nova_arm_ws`](../nova_arm_ws).

The original system was developed to monitor the Nova Arm's hardware state, identify faults, and perform predefined recovery procedures when possible. This project expands that concept beyond the arm to create a **robot-wide health monitoring and conversational system**.

The goal is to allow the robot to monitor the health of its major hardware and software subsystems, respond to known faults, and communicate its condition to humans through natural language.

Development will be carried out jointly by the **Hardware** and **NLP** teams. Hardware members will define subsystem health information, fault conditions, diagnostics, and recovery procedures, while the NLP team will develop the conversational interface that allows humans to query and interact with this information.

### Initial Goals

* Extend health monitoring from the Nova Arm to other robot subsystems.
* Monitor components such as the pan/tilt system, mobile base, sensors, and other critical hardware.
* Implement predefined diagnostic and recovery procedures for known faults.
* Provide a common interface for reporting subsystem health.
* Develop a natural-language interface that allows users to ask questions such as:

  * "Are you okay?"
  * "Is your arm working?"
  * "What's wrong with you?"
  * "Did you fix the motor?"
* Allow the conversational system to retrieve information from the robot's health monitoring system without directly controlling hardware.
* Maintain a modular architecture so that individual Hardware and NLP components can be developed and tested independently.

### Design Philosophy

The existing Nova Arm system provides the foundation for this project. Rather than replacing it with a monolithic system, the project will build upon its existing diagnostic and recovery capabilities and extend the same concept to the rest of the robot.

The conversational layer should act as an interface to the robot's existing capabilities rather than directly controlling hardware. In general:

**Hardware systems determine the robot's actual state → Health systems diagnose and recover faults → NLP interprets human requests and communicates the results.**

This separation allows the robot to communicate naturally while keeping hardware control and safety-critical behavior within dedicated systems.

As development progresses, the system can be expanded with additional subsystems, diagnostic capabilities, recovery procedures, and conversational functions.

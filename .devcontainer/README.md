# Java development environment

This repository's Android build and CI use JDK 17. The Dev Container configuration installs Eclipse Temurin JDK 17 and verifies both `java` and `javac` on container creation.

To use it, open this repository in a Dev Containers-compatible editor or Codespaces and choose **Reopen in Container** (or rebuild the container). The configuration applies when that development container is created; it does not modify an already-running Arena shell unless Arena launches the workspace from this Dev Container configuration.

Android SDK setup is separate. The GitHub Actions workflows already install JDK 17 and configure the Android SDK for CI.

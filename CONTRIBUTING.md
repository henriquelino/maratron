# Contributing to Maratron

Thanks for your interest in Maratron, which turns a manual treadmill into game input. Contributions of code, docs, hardware notes, and game-compatibility reports are all welcome.

By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Ways to help

- Report a bug or request a feature through the issue templates.
- Improve the [build and use guide](docs/guide/README.md).
- Share game-compatibility results (which games work with gamepad or VR output).
- Fix a bug or add a feature with a pull request.

## Development setup

Maratron runs on Windows with Python 3.10 or newer.

1. Create and activate a virtual environment:

   ```
   python -m venv .venv
   .\.venv\Scripts\activate
   ```

2. Install the dependencies:

   ```
   pip install -r requirements.txt
   ```

3. Run the dashboard without hardware:

   ```
   python python/run.py --mock
   ```

The `--mock` flag gives you a slider to simulate walking, so you can work on most of the app with no ESP32 connected.

## Running the tests

Run the test suite from the virtual environment. The base interpreter does not have pydantic or fastapi.

```
pytest python/tests
```

Add or update tests when you change the control math, the models, or the session logic.

## Firmware and the VR driver

- Firmware: the single Arduino sketch in `arduino/treadmill_to_py/`. See guide [part 1](docs/guide/01-hardware-build.md) and [part 2](docs/guide/02-software-setup.md).
- SteamVR driver: C++ in `vr_driver/`. Build it with `vr_driver/build.ps1`. Close SteamVR first, or the link step fails with `LNK1104`, because SteamVR holds the driver DLL open. The shared-memory struct in `vr_ipc.py` and `driver_maratron.cpp` must stay byte-identical.

## Coding conventions

- Match the style of the code around your change.
- Keep the control loop (engine, hardware output, VR write) free of extra per-iteration work.
- Documentation uses plain, simple English: short imperative steps, no em dashes, no emojis. Write the English version first.
- Do not delete a working feature or leave it as a dead UI knob. Default the good path and, if needed, keep the old behavior behind a startup flag. Pre-dashboard scripts live locally in `.legacy/`, which is not tracked.

## Pull requests

1. Branch from `main`.
2. Keep each pull request focused on one change.
3. Make sure `pytest python/tests` passes.
4. Update the guide in `docs/` if you change behavior a user would notice.
5. Do not commit secrets, personal data, or image location metadata.
6. Fill in the pull request template.

Contributions are licensed under the project's [GPL-3.0 license](LICENSE).

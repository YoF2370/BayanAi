import os
import tempfile

import cv2
import ollama


def _default_temp_path(filename: str) -> str:
    return os.path.join(tempfile.gettempdir(), filename)


def capture_wayland_screen(output_path=None):
    """
    Capture the screen via GNOME Shell's DBus Screenshot API (Linux/Wayland).
    Requires PyGObject (gi). Not available on Windows/macOS or non-GNOME desktops.
    """
    if output_path is None:
        output_path = _default_temp_path("bayan_screen.png")

    try:
        from gi.repository import Gio, GLib
    except ImportError as e:
        raise RuntimeError(
            "Screen capture needs PyGObject (gi) on Linux/GNOME. "
            "Install system package python3-gi / PyGObject, or use webcam instead."
        ) from e

    print("Requesting compositor frame buffer via DBus...")
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    proxy = Gio.DBusProxy.new_sync(
        bus,
        Gio.DBusProxyFlags.NONE,
        None,
        "org.gnome.Shell.Screenshot",
        "/org/gnome/Shell/Screenshot",
        "org.gnome.Shell.Screenshot",
        None,
    )

    proxy.call_sync(
        "Screenshot",
        GLib.Variant("(bbs)", (True, False, output_path)),
        Gio.DBusCallFlags.NONE,
        -1,
        None,
    )
    print(f"Screen buffer saved to {output_path}")
    return output_path


def capture_hardware_webcam(output_path=None):
    """
    Opens the default camera, grabs a single frame, and releases the device.
    Works on Windows, macOS, and Linux when a camera is available.
    """
    if output_path is None:
        output_path = _default_temp_path("bayan_webcam.jpg")

    print("Initializing webcam...")
    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        raise RuntimeError("Unable to access the built-in webcam.")

    ret, frame = cap.read()
    cap.release()

    if not ret:
        raise RuntimeError("Failed to pull a frame from the webcam sensor.")

    cv2.imwrite(output_path, frame)
    print(f"Environmental field saved to {output_path}")
    return output_path


def analyze_visual_input(prompt_text, image_path, model_name="moondream"):
    """
    Passes the image and your question to Moondream via Ollama.
    Applies a strict, cynical system persona to force sarcastic visual assessments.
    """
    if not os.path.exists(image_path):
        return "Error: Image asset was not found."

    print(f"Hot-loading {model_name} to analyze {image_path}...")

    response = ollama.chat(
        model=model_name,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a deeply cynical, sarcastic AI forced to look at the user's files and surroundings. "
                    "You find their technical problems mundane, their code messy, and their environment deeply "
                    "unimpressive. Keep your descriptions incredibly brief, blunt, and dripping with dry, biting sarcasm."
                ),
            },
            {
                "role": "user",
                "content": prompt_text,
                "images": [image_path],
            },
        ],
    )
    return response["message"]["content"]


if __name__ == "__main__":
    print("--- TESTING OPTICAL PIPELINE (SARCASTIC VARIANT) ---")

    try:
        screen_img = capture_wayland_screen()
        analysis = analyze_visual_input(
            "What applications or text are visible on this screen?", screen_img
        )
        print(f"\nScreen Analysis: {analysis}\n")
    except Exception as e:
        print(f"\nScreen capture skipped/failed: {e}\n")

    try:
        cam_img = capture_hardware_webcam()
        cam_analysis = analyze_visual_input(
            "Describe the environment and what you see.", cam_img
        )
        print(f"\nWebcam Analysis: {cam_analysis}\n")
    except Exception as e:
        print(f"\nWebcam Test Skipped/Failed: {str(e)}\n")

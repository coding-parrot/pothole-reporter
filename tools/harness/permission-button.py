#!/usr/bin/env python3
"""Print the screen coordinates of the grant button in an Android permission dialog.

Reads a uiautomator XML dump on stdin and writes "<x> <y>" for the button that grants
the permission for normal use. Native permission dialogs are exposed to accessibility
even when the app's own WebView is not, so this is exact where pixel matching is a guess:
the dialog's height changes with its content, and its colours follow the system theme.

Exits 1 when no grant button is present, which is how the caller learns the dialog is gone.
"""

import re
import sys

# Most specific first. "Only this time" grants a one-shot permission and "Don't allow"
# denies, so neither is ever chosen here.
PREFERRED = [
    "while using the app",
    "allow only while using the app",
    "allow while using the app",
    "allow all the time",
    "precise",
    "allow",
]

NODE = re.compile(r'text="([^"]*)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"')


def main():
    dump = sys.stdin.read()
    buttons = []
    for match in NODE.finditer(dump):
        label = match.group(1).strip()
        if not label:
            continue
        x1, y1, x2, y2 = (int(match.group(i)) for i in range(2, 6))
        buttons.append((label.lower(), (x1 + x2) // 2, (y1 + y2) // 2))

    for wanted in PREFERRED:
        for label, x, y in buttons:
            if label == wanted:
                print(f"{x} {y}")
                return 0
    # Fall back to a prefix match so a reworded button ("Allow", "Allow access") still
    # works, while still refusing the deny and one-shot options.
    for label, x, y in buttons:
        if label.startswith("allow") and "only this time" not in label:
            print(f"{x} {y}")
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

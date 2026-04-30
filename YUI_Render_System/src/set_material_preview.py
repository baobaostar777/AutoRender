from __future__ import annotations

import bpy  # type: ignore


def main() -> None:
    wm = bpy.context.window_manager
    wins = wm.windows if wm else []
    ok = False
    for win in wins:
        screen = win.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            for sp in area.spaces:
                if sp.type == "VIEW_3D":
                    sp.shading.type = "MATERIAL"
                    ok = True
    print("[preview] shading MATERIAL set" if ok else "[preview] no VIEW_3D area found")


if __name__ == "__main__":
    main()

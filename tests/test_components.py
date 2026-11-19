from __future__ import annotations

import unittest

import discord

from bot.cogs.container import PendingChord, TerminalControlView
from bot.xorg import XorgControlState, XorgControlView


class TerminalComponentTests(unittest.TestCase):
    def buttons(self, view: TerminalControlView) -> dict[str, discord.ui.Button]:
        return {
            item.custom_id: item
            for item in view.children
            if isinstance(item, discord.ui.Button) and item.custom_id
        }

    def test_view_is_persistent_and_has_all_controls(self) -> None:
        view = TerminalControlView(None)  # type: ignore[arg-type]
        buttons = self.buttons(view)

        self.assertTrue(view.is_persistent())
        self.assertEqual(
            set(buttons),
            {
                "alpine-tty:up",
                "alpine-tty:down",
                "alpine-tty:left",
                "alpine-tty:right",
                "alpine-tty:ctrl",
                "alpine-tty:alt",
                "alpine-tty:escape",
                "alpine-tty:tab",
                "alpine-tty:send",
            },
        )
        self.assertTrue(buttons["alpine-tty:send"].disabled)

    def test_selected_chord_changes_button_styles(self) -> None:
        view = TerminalControlView(
            None,  # type: ignore[arg-type]
            PendingChord(ctrl=True, alt=False, key="up"),
        )
        buttons = self.buttons(view)

        self.assertEqual(
            buttons["alpine-tty:ctrl"].style,
            discord.ButtonStyle.primary,
        )
        self.assertEqual(
            buttons["alpine-tty:up"].style,
            discord.ButtonStyle.primary,
        )
        self.assertFalse(buttons["alpine-tty:send"].disabled)
        self.assertEqual(
            buttons["alpine-tty:send"].style,
            discord.ButtonStyle.success,
        )


class XorgComponentTests(unittest.TestCase):
    def buttons(self, view: XorgControlView) -> dict[str, discord.ui.Button]:
        return {
            item.custom_id: item
            for item in view.children
            if isinstance(item, discord.ui.Button) and item.custom_id
        }

    def test_view_is_persistent_and_has_xorg_controls(self) -> None:
        view = XorgControlView(None)
        buttons = self.buttons(view)

        self.assertTrue(view.is_persistent())
        self.assertEqual(
            set(buttons),
            {
                "alpine-xorg:up",
                "alpine-xorg:down",
                "alpine-xorg:left",
                "alpine-xorg:right",
                "alpine-xorg:step-100",
                "alpine-xorg:step-10",
                "alpine-xorg:step-1",
                "alpine-xorg:refresh",
                "alpine-xorg:left-click",
                "alpine-xorg:right-click",
                "alpine-xorg:ctrl",
                "alpine-xorg:alt",
                "alpine-xorg:escape",
                "alpine-xorg:tab",
                "alpine-xorg:tty",
            },
        )
        self.assertEqual(
            buttons["alpine-xorg:step-10"].style,
            discord.ButtonStyle.primary,
        )

    def test_held_buttons_and_modifiers_are_highlighted(self) -> None:
        view = XorgControlView(
            None,
            XorgControlState(
                step=1,
                ctrl=True,
                left_down=True,
            ),
        )
        buttons = self.buttons(view)

        self.assertEqual(
            buttons["alpine-xorg:step-1"].style,
            discord.ButtonStyle.primary,
        )
        self.assertEqual(
            buttons["alpine-xorg:ctrl"].style,
            discord.ButtonStyle.primary,
        )
        self.assertEqual(
            buttons["alpine-xorg:left-click"].style,
            discord.ButtonStyle.primary,
        )


if __name__ == "__main__":
    unittest.main()

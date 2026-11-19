from __future__ import annotations

import unittest

import discord

from bot.cogs.container import PendingChord, TerminalControlView


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


if __name__ == "__main__":
    unittest.main()

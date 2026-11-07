"""Personas: how each simulated user perceives and operates the browser.

Three layers, from most to least objective:
1. Chrome rendering (launch flags + DevTools emulation): what the agent *sees* in its screenshots.
2. Input filtering in our bridge: what the agent *can do* (tremor jitter, no mouse).
3. Instructions: how the agent *behaves* (patience, vocabulary). Kept minimal on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Persona:
    id: str
    label: str
    description: str  # told to the agent
    zoom: float = 1.0  # browser zoom: CSS viewport shrinks by this factor (media queries react), screenshot size stays
    chrome_flags: tuple[str, ...] = ()
    vision_deficiency: str | None = None  # CDP Emulation.setEmulatedVisionDeficiency type
    media_features: tuple[tuple[str, str], ...] = ()  # CDP Emulation.setEmulatedMedia features
    click_jitter_px: int = 0  # random offset added to every pointer action
    keyboard_only: bool = False  # mouse commands fail, typing goes to the focused element
    tags: tuple[str, ...] = field(default=())


PERSONAS: dict[str, Persona] = {
    p.id: p
    for p in [
        Persona(
            id="baseline",
            label="Baseline user",
            description="You are a typical sighted adult using a mouse and keyboard, with no impairment.",
            tags=("baseline",),
        ),
        Persona(
            id="low_vision",
            label="Low vision (200% zoom + blur)",
            description=(
                "You have low vision. You browse with the page zoomed to 200% and you still see a blurry image. "
                "You cannot change the zoom level. Only trust text you can actually read in the screenshot."
            ),
            zoom=2.0,
            vision_deficiency="blurredVision",
            tags=("vision",),
        ),
        Persona(
            id="color_blind",
            label="Colour blind (deuteranopia)",
            description=(
                "You have red-green colour blindness (deuteranopia). You see the page exactly as shown in the "
                "screenshot; do not assume colours you cannot actually distinguish."
            ),
            vision_deficiency="deuteranopia",
            tags=("vision",),
        ),
        Persona(
            id="tremor",
            label="Hand tremor (imprecise pointer)",
            description=(
                "You have a hand tremor: your mouse clicks often land a little off target. "
                "Check after each click that the intended thing happened."
            ),
            click_jitter_px=10,
            tags=("motor",),
        ),
        Persona(
            id="keyboard_only",
            label="Keyboard only (no mouse)",
            description=(
                "You cannot use a mouse. You navigate only with the keyboard: Tab / Shift+Tab to move focus, "
                "Enter or Space to activate, arrow keys, and typing into the focused field. "
                "Any mouse action will fail."
            ),
            keyboard_only=True,
            tags=("motor",),
        ),
    ]
}


COMMON_RULES = """You are a usability tester acting as a real end user of a website.
Persona: {persona}

Rules:
- Use the website only through its interface (click, type, scroll, keyboard), the way a real user would.
- Never type a URL into the address bar except the start URL. Never use browser zoom, developer tools or page scripts.
- Rely only on what you actually perceive in the screenshots. Do not guess hidden information.
- If you are stuck after a few reasonable attempts, stop and report failure. Do not loop.
- At the end, report honestly whether you completed the goal, the information the goal asks for, and every
  obstacle you met (what, where, how bad)."""

LOGIN_RULES = """
Signing in: if you reach a sign-in page, use the test account through the `fill_credential` tool. Put the focus in
the email/username field (click it, or Tab to it), call fill_credential(field="username"), then do the same for the
password field with field="password", then submit the form. You never see the values; do not try to type them."""

VAULT_LOGIN_RULES = """
Signing in: if you reach a sign-in page, use the saved credentials of the test account (the browser's credential
vault fills them for you). You never see the values; do not try to type them."""


def build_instructions(persona: Persona, login_mode: str | None) -> str:
    """login_mode: None (no login), "tool" (fill_credential custom tool) or "vault" (driver vault hook)."""
    text = COMMON_RULES.format(persona=persona.description)
    if login_mode == "tool":
        text += LOGIN_RULES
    elif login_mode == "vault":
        text += VAULT_LOGIN_RULES
    return text

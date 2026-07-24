"""Pure grammar for commands spoken after the local "Mumble" activation.

Parsing is deliberately separate from execution so wake-word detection grants no
authority. The controller decides whether each returned intent is currently safe.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceCommand:
    intent: str
    value: str = ""


_MODE = re.compile(
    r"^(?:(?:switch|change|set|go)\s+(?:to\s+)?|use\s+)?"
    r"(text|plain|prompt|email|reply|foreign)\s+mode[.!]?$",
    re.IGNORECASE,
)
_SEARCH = re.compile(
    r"^(?:search(?:\s+the)?\s+web(?:\s+for)?|search\s+for|look\s+up)\s+(.+)$",
    re.IGNORECASE,
)


def parse_voice_command(text):
    command = " ".join(str(text or "").strip().split())
    if not command:
        return VoiceCommand("empty")
    lowered = command.casefold().rstrip(".!?")
    if lowered in {"stop", "cancel", "never mind", "nevermind", "stop control"}:
        return VoiceCommand("cancel")
    if lowered in {"open deck", "open the deck", "show deck", "show the deck",
                   "deck", "open history", "show history"}:
        return VoiceCommand("deck")
    if lowered in {"start dictation", "dictate", "start listening", "take dictation"}:
        return VoiceCommand("dictate")
    match = _SEARCH.match(command)
    if match:
        return VoiceCommand("search", match.group(1).strip().rstrip(".!?"))
    match = _MODE.match(command)
    if match:
        mode = match.group(1).casefold()
        return VoiceCommand("mode", "text" if mode == "plain" else mode)
    control = re.match(
        r"^(?:control\s+(?:my\s+)?computer(?:\s+and)?|computer\s+command|control)\s*[:,-]?\s*(.+)$",
        command,
        re.IGNORECASE,
    )
    if control:
        return VoiceCommand("control", control.group(1).strip())
    # Natural desktop requests remain supported, but they still pass through the
    # separate opt-in, planning, policy, and confirmation gates.
    return VoiceCommand("control", command)


__all__ = ["VoiceCommand", "parse_voice_command"]

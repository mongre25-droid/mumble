from voice_commands import parse_voice_command


def test_general_voice_activation_grammar():
    cases = {
        "open the Deck": ("deck", ""),
        "search the web for Cerebras pricing": ("search", "Cerebras pricing"),
        "switch to reply mode": ("mode", "reply"),
        "plain mode": ("mode", "text"),
        "start dictation": ("dictate", ""),
        "never mind": ("cancel", ""),
        "control my computer and open Notepad": ("control", "open Notepad"),
        "open Notepad": ("control", "open Notepad"),
    }
    for spoken, expected in cases.items():
        parsed = parse_voice_command(spoken)
        assert (parsed.intent, parsed.value) == expected


def test_empty_voice_command_is_not_control_authority():
    parsed = parse_voice_command("   ")
    assert parsed.intent == "empty"
    assert parsed.value == ""

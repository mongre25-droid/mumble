"""Scenario packs for the eval harness.

Each scenario is a (id, lane, input_text) tuple. The harness runs each scenario
through the configured providers and scores the output.

Lanes:
- dictation: raw speech-to-text polish (cleanup, punctuation, grammar)
- prompt: turn spoken request into a ready-to-use AI prompt
- email: generate a formatted email from dictated content
- reply: context-aware reply to a message
- foreign: resolve foreign-language uncertainty markers

Each scenario has an expected characteristic (not an exact match).
"""

# ---------------------------------------------------------------------------
# Basic scenario pack — representative inputs for quick comparison
# ---------------------------------------------------------------------------

BASIC_SCENARIOS = [
    # === Dictation / polish lane ===
    ("dict-short", "dictation",
     "hello world this is a test of the dictation system i hope it works well"),

    ("dict-mid", "dictation",
     "the meeting is at three o clock on tuesday we need to discuss the Q3 "
     "budget and the new hiring plan also we should talk about the office move"),

    ("dict-fillers", "dictation",
     "um i think uh we should you know consider the proposal and um get back "
     "to them by friday"),

    # === Prompt lane ===
    ("prompt-simple", "prompt",
     "make a prompt for a python script that renames all files in a folder "
     "to lowercase"),

    ("prompt-mid", "prompt",
     "i need a prompt for an AI that acts as a senior code reviewer it should "
     "check for bugs security issues and style problems"),

    ("prompt-short", "prompt",
     "write a prompt for a haiku about programming"),

    # === Email lane ===
    ("email-short", "email",
     "email hi john just checking in on the Q3 budget report any updates"),

    ("email-formal", "email",
     "email dear professor williams i would like to request an extension on "
     "the research paper due to unforeseen circumstances"),

    # === Reply lane ===
    ("reply-short", "reply",
     "reply say yes that works for me see you at three"),

    ("reply-decline", "reply",
     "reply say i cant make it on friday but how about monday instead"),

    # === Foreign lane ===
    ("foreign-short", "foreign",
     "the meaning of salam//salaam//Islam is peace and the word "
     "Quran//koran is the holy book"),

    ("foreign-multi", "foreign",
     "i need to learn Arabic//Urdu//French for my trip to "
     "Marrakech//Marrakesh//Morocco next summer"),

    # === Nonsense / garbage input ===
    ("nonsense-short", "dictation",
     "asdfghjkl qwertyuiop zxcvbnm"),
]


# ---------------------------------------------------------------------------
# Extended scenario pack — more comprehensive coverage
# ---------------------------------------------------------------------------

EXTENDED_SCENARIOS = BASIC_SCENARIOS + [
    # === Dictation: longer inputs ===
    ("dict-long", "dictation",
     "i have been working on the authentication module for the past two "
     "weeks and i think i finally got the token refresh logic working "
     "correctly the main challenge was handling the edge case where the "
     "refresh token expires at the same time as the access token i solved "
     "it by adding a grace period of thirty seconds during which we retry "
     "with a new token before logging the user out"),

    ("dict-very-long", "dictation",
     "good morning team today i want to cover three main topics for our "
     "sprint planning first we need to finalize the API design for the "
     "payment gateway integration this has been dragging on for two sprints "
     "and we need to lock it down second the QA team has flagged several "
     "regression issues in the user profile module that need immediate "
     "attention third we should discuss the timeline for the mobile app "
     "release which is currently slated for the end of this quarter i would "
     "also like to get everyone's input on the new code review process that "
     "we piloted last week the feedback has been mostly positive but there "
     "are a few concerns about the time commitment lets go around the room "
     "and hear from each team lead"),

    ("dict-numbers", "dictation",
     "the budget for Q3 is one hundred and fifty thousand dollars we expect "
     "to spend about forty percent on engineering thirty percent on marketing "
     "and the remaining thirty percent on operations"),

    # === Prompt: complex requests ===
    ("prompt-complex", "prompt",
     "design a prompt for an AI writing assistant that helps with technical "
     "documentation it should understand the codebase structure generate API "
     "docs from code comments and maintain consistent style across the "
     "entire project"),

    ("prompt-creative", "prompt",
     "create a prompt for generating a fantasy world with unique magic "
     "systems cultures and a detailed map"),

    # === Email: longer and edge cases ===
    ("email-long", "email",
     "email dear team i wanted to follow up on our discussion from the "
     "sprint planning meeting we agreed to prioritize the payment gateway "
     "integration this week the estimated effort is around forty story "
     "points and we need to coordinate with the frontend team on the "
     "checkout flow please let me know if you have any concerns or "
     "blockers by tomorrow end of day thanks"),

    ("email-informal", "email",
     "email hey sarah want to grab lunch on thursday the new thai place "
     "downtown looks great"),

    # === Reply: with context ===
    ("reply-context", "reply",
     "reply tell them i can present at the meeting and i will prepare "
     "slides by wednesday"),

    # === Foreign: more languages and patterns ===
    ("foreign-uncertain", "foreign",
     "the word for hello in Arabic//French//Spanish is "
     "marhaba//bonjour//hola depending on where you are"),

    ("foreign-names", "foreign",
     "my friend Muhammad//Mohammed//Mohamed is coming to visit with his "
     "family from Islamabad//Islam Abad//Pakistan"),

    # Garbage edge cases
    ("nonsense-long", "dictation",
     "zxcv zxcv zxcv zxcv zxcv zxcv zxcv qwer qwer qwer asdf asdf asdf"),
]


def get_scenarios(name):
    """Return scenario list by name ('basic' or 'extended').

    Unknown names fall back to BASIC_SCENARIOS.
    """
    return {"basic": BASIC_SCENARIOS, "extended": EXTENDED_SCENARIOS}.get(
        name, BASIC_SCENARIOS
    )


def lane_counts(scenarios):
    """Return a dict of lane -> count for a scenario list."""
    counts = {}
    for _sid, lane, _text in scenarios:
        counts[lane] = counts.get(lane, 0) + 1
    return counts

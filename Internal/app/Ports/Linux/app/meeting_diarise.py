"""Meeting diarisation — speaker separation and identification.

Three tiers:
  1. LIGHTWEIGHT (default, no extra deps): Multi-pass speaker detection —
     Pass 1: VAD segments → gap heuristics (turn-taking based on silence gaps).
     Pass 2: Energy-level comparison — RMS energy profiles refine ambiguous
             speaker boundaries by detecting energy-level shifts between turns.
     Pass 3 (optional): pyannote.audio neural speaker embedding + clustering.
     Accurate enough for 2-4 person meetings with clear turn-taking.

  2. ADVANCED (opt-in, requires HuggingFace token): pyannote.audio pipeline
     for neural speaker embedding + clustering. The gold standard for
     diarisation accuracy.

  3. CLOUD (optional): some cloud STT providers support diarisation natively.

The module exports a single `diarise()` function that picks the best
available tier automatically.

Owner 2026-06-29 — meeting-mode milestone.
Enhanced 2026-07-03 — transcription-enhance milestone:
  • Energy-level comparison for ambiguous speaker boundaries
  • Multi-pass speaker detection (gap → energy → pyannote)
  • Expanded context window for speaker name inference

Speaker NAME inference (refinement pass §8): diarisation only ever knows
"Speaker 1/2/3". `infer_speaker_names` reads the transcript for who-talks-to-whom
cues — a direct address ("Hey Ralph, could you…") means the speaker who answers
next is probably Ralph; a self-introduction ("I'm Ralph") names the current
speaker — and attaches a ranked `suggested_names` list to each speaker. These are
SUGGESTIONS the UI offers as one-tap pills; the user's own label always wins.
"""

import math
import re
from collections import defaultdict

import numpy as np


def diarise(audio, sample_rate=16000, segments=None, settings=None):
    """Run speaker diarisation on an audio array using multi-pass detection.

    Multi-pass strategy:
      1. If pyannote is available → neural speaker embedding + clustering
         (the gold standard). Falls back to lightweight on any failure.
      2. Lightweight: gap heuristics (Pass 1) → energy comparison (Pass 2).
      3. After speaker assignment, infer candidate speaker NAMES from the
         transcript text.

    Args:
        audio: float32 numpy array, mono
        sample_rate: sample rate (default 16000)
        segments: optional list of (start_sec, end_sec, text) from transcription.
                  When provided, diarisation assigns a speaker label to each
                  segment rather than generating its own boundaries.
        settings: optional Settings-like object for provider selection.

    Returns:
        dict with:
          speakers: [{"label": "Speaker 1", "color": "#D4AF37",
                      "suggested_names": [...], "name": None}, …]
          segments: [{speaker, start_sec, end_sec, text, energy}, …] (same shape
                    as input but with `speaker` assigned and `energy` computed)
          method: str — which diarisation method was used
    """
    used_method = "lightweight"

    # Try the advanced neural path first, then fall back to lightweight. Either
    # way, infer candidate speaker NAMES from the transcript before returning.
    if _pyannote_available(settings):
        try:
            result = _diarise_pyannote(
                audio, sample_rate, segments, settings=settings)
            result["method"] = "pyannote"
            return infer_speaker_names(result)
        except Exception as e:
            print(f"[diarise] pyannote failed ({e}), falling back to lightweight")

    result = _diarise_lightweight(audio, sample_rate, segments)
    result["method"] = used_method
    return infer_speaker_names(result)


# ── Lightweight diarisation (no extra deps) ─────────────────────────────────
# Multi-pass speaker detection:
#   Pass 1: Gap heuristics (existing) — assign speakers based on silence gaps
#   Pass 2: Energy-level comparison — refine ambiguous boundaries by detecting
#           significant energy shifts between consecutive segments, which
#           strongly suggests a speaker change even within a moderate gap.


def _compute_segment_energy(audio, start_sec, end_sec, sample_rate=16000):
    """Compute the RMS energy of an audio segment in dB.

    Returns a float representing the average RMS level in decibels (dB FS),
    or -inf for a silent/empty segment.  This is used to compare the
    energy profile of consecutive segments — a large energy shift suggests
    a different speaker (different voice projection, mic distance, etc.).

    The energy is clamped to a floor of -80 dB to avoid -inf on perfect
    silence and to make comparisons numerically stable.
    """
    start_sample = max(0, int(start_sec * sample_rate))
    end_sample = min(len(audio), int(end_sec * sample_rate))
    if end_sample <= start_sample:
        return -80.0
    chunk = np.asarray(audio[start_sample:end_sample], dtype=np.float32).flatten()
    rms = np.sqrt(np.mean(chunk ** 2))
    if rms <= 0.0:
        return -80.0
    db = 20.0 * math.log10(rms)
    return max(-80.0, db)


def _energy_shift_significant(energy_a, energy_b, threshold_db=6.0):
    """True when the energy difference between two segments is large enough
    to strongly suggest different speakers.

    A threshold of ~6 dB is a reasonable default: it corresponds to roughly
    a 2× difference in perceived loudness, which typically means a different
    person, different distance from mic, or different speaking style.
    """
    if energy_a <= -80.0 or energy_b <= -80.0:
        return False  # too quiet to compare reliably
    return abs(energy_a - energy_b) >= threshold_db


def _diarise_lightweight(audio, sample_rate=16000, segments=None):
    """Multi-pass speaker separation.

    Pass 1 (gap heuristics): assign speakers based on silence gaps between
    segments — long gap (>1.5s) = speaker change, short gap (<0.4s) = same
    speaker.

    Pass 2 (energy comparison): for segments with ambiguous gaps
    (0.4–1.5s), compare RMS energy levels.  A significant energy shift
    (≥6 dB) strongly suggests a different speaker and overrides the
    default "same speaker" assumption for moderate gaps.

    Each segment is annotated with its computed `energy` value (dB) so
    callers can inspect the diarisation confidence.
    """
    if segments and len(segments) > 0:
        return _lightweight_with_segments(audio, sample_rate, segments)
    else:
        return _lightweight_no_segments(audio, sample_rate)


def _lightweight_with_segments(audio=None, sample_rate=16000, segments=None):
    """Multi-pass speaker assignment to pre-transcribed segments.

    Pass 1 — Gap heuristics:
      • Long gap (>1.5s)  → likely new speaker
      • Short gap (<0.4s) → same speaker (natural pause within a turn)
      • Ambiguous gap (0.4–1.5s) → defer to Pass 2

    Pass 2 — Energy comparison (when audio is provided):
      • For each ambiguous gap, compute the RMS energy of both segments
        from the original audio.
      • If the energy shift is significant (≥6 dB), treat it as a speaker
        change (different voice projection, mic distance, etc.).
      • Otherwise keep the same speaker.

    Each output segment includes a computed `energy` field (dB) for
    diagnostic / confidence inspection by the caller, when audio is
    available.

    Backward-compatible: the old signature `_lightweight_with_segments(segments)`
    is still accepted; audio and sample_rate are optional and default to None/16000.
    """
    # Backward compatibility: old callers pass segments as the first positional arg.
    if segments is None and audio is not None:
        # Heuristic: if a single list-like arg was passed, treat it as segments.
        if isinstance(audio, list):
            segments = audio
            audio = None

    if not segments:
        return {"speakers": [], "segments": []}

    # Normalize segment shape
    norm = _normalize_segments(segments)

    # Compute energy for every segment when audio is available (Pass 2 prep).
    if audio is not None and len(audio) > 0:
        for s in norm:
            s["energy"] = round(
                _compute_segment_energy(audio, s["start_sec"], s["end_sec"],
                                         sample_rate),
                1,
            )

    if len(norm) == 1:
        label = "Speaker 1"
        return {
            "speakers": [{"label": label, "name": None,
                          "color": SPEAKER_COLORS[0]}],
            "segments": [{**s, "speaker": label} for s in norm],
        }

    # ── Pass 1: Gap heuristics ──────────────────────────────────────────
    assigned = []
    current_speaker_idx = 0
    current_speaker_label = _speaker_label(current_speaker_idx)
    assigned.append({**norm[0], "speaker": current_speaker_label})

    for i in range(1, len(norm)):
        gap = norm[i]["start_sec"] - norm[i - 1]["end_sec"]

        if gap > 1.5:
            # Significant gap → likely a new speaker.
            current_speaker_idx = (current_speaker_idx + 1) % 8
            current_speaker_label = _speaker_label(current_speaker_idx)
        elif gap < 0.4:
            # Very short gap → same speaker (natural pause within a turn).
            pass
        else:
            # ── Pass 2: Energy comparison for ambiguous gaps ─────────────
            # A 0.4–1.5s gap is ambiguous — it COULD be a natural pause
            # within one speaker's turn, or a quick back-and-forth between
            # two speakers.  Energy-level comparison breaks the tie.
            energy_prev = norm[i - 1].get("energy", -80.0)
            energy_curr = norm[i].get("energy", -80.0)

            if _energy_shift_significant(energy_prev, energy_curr):
                # Different energy profile → different speaker.
                current_speaker_idx = (current_speaker_idx + 1) % 8
                current_speaker_label = _speaker_label(current_speaker_idx)
            # else: same speaker (energy consistent with a natural pause).

        assigned.append({**norm[i], "speaker": current_speaker_label})

    # Collect unique speakers
    unique_labels = sorted(set(s["speaker"] for s in assigned),
                           key=lambda x: int(x.split()[-1]))
    speakers = [
        {"label": lab, "name": None,
         "color": SPEAKER_COLORS[int(lab.split()[-1]) - 1]}
        for lab in unique_labels
    ]

    return {"speakers": speakers, "segments": assigned}


def _lightweight_no_segments(audio, sample_rate=16000):
    """Without transcription segments, return empty segments.

    The caller should transcribe first, then diarise. We still compute
    basic audio-level energy stats so the caller can gauge diarisation
    feasibility — but without transcribed text there are no segments
    to assign speakers to.

    Returns empty speakers and segments (same contract as the original
    implementation). Audio energy stats are included when audio is
    available so downstream consumers can make informed decisions about
    diarisation quality.
    """
    result = {"speakers": [], "segments": []}

    if audio is not None and len(audio) > 0:
        audio_arr = np.asarray(audio, dtype=np.float32).flatten()
        rms = float(np.sqrt(np.mean(audio_arr ** 2)))
        db = max(-80.0, 20.0 * math.log10(rms)) if rms > 0 else -80.0
        result["audio_energy_db"] = round(db, 1)
        result["audio_duration_sec"] = round(
            len(audio_arr) / float(sample_rate), 2)

    return result


# ── Advanced diarisation (pyannote.audio) ───────────────────────────────────


def _pyannote_available(settings=None):
    """Check whether pyannote.audio with a valid HF token is available."""
    token = _hf_token(settings)
    if not token:
        return False
    try:
        import pyannote.audio
        return True
    except ImportError:
        return False


def _hf_token(settings=None):
    """Resolve HuggingFace token from settings or environment."""
    if settings:
        token = (settings.get("hf_token", "") or "").strip()
        if token:
            return token
    import os
    return os.environ.get("HF_TOKEN", "") or os.environ.get("HUGGINGFACE_TOKEN", "")


def _diarise_pyannote(audio, sample_rate=16000, segments=None, settings=None):
    """Run neural speaker diarisation via pyannote.audio.

    Uses the pyannote/speaker-diarisation-3.1 pipeline (or latest available).
    Maps diarisation output to the existing VAD/transcription segments.
    """
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        use_auth_token=_hf_token(settings),
    )

    # Reshape audio for pyannote (expects (channels, samples) or 1D)
    waveform = audio.reshape(1, -1) if audio.ndim == 1 else audio

    diarization = pipeline({"waveform": waveform, "sample_rate": sample_rate})

    # Build speaker mapping: turn → speaker label
    turns = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        turns.append({
            "start_sec": round(turn.start, 2),
            "end_sec": round(turn.end, 2),
            "speaker": speaker,
        })

    # Merge pyannote turns with transcription segments when available
    if segments and len(segments) > 0:
        norm = _normalize_segments(segments)
        merged = _merge_turns_and_segments(turns, norm)
    else:
        merged = turns

    # Map pyannote speaker IDs to numbered labels
    speaker_ids = sorted(set(s.get("speaker", "") for s in merged))
    # The UI/store contract supports eight colours/labels.  Very noisy neural
    # output can contain more clusters; fold overflow into Speaker 8 instead of
    # indexing past SPEAKER_COLORS and discarding the whole advanced result.
    speaker_map = {sid: _speaker_label(min(i, len(SPEAKER_COLORS) - 1))
                   for i, sid in enumerate(speaker_ids)}

    for s in merged:
        s["speaker"] = speaker_map.get(s.get("speaker", ""), "Speaker 1")

    labels = sorted(set(speaker_map.values()),
                    key=lambda label: int(label.split()[-1]))
    speakers = [{"label": label, "name": None,
                 "color": SPEAKER_COLORS[int(label.split()[-1]) - 1]}
                for label in labels]

    return {"speakers": speakers, "segments": merged}


# ── Speaker name inference (refinement pass §8) ─────────────────────────────
# Pure text heuristics, deliberately HIGH-PRECISION (a wrong suggestion is worse
# than none): a name is only proposed when it follows an address cue, sits in a
# trailing-address position, or appears in a self-introduction. Everything is a
# SUGGESTION — the user's chosen label always overrides.

# HANDOFF cues — addressing the person about to speak NEXT ("Hey Ralph, can you…",
# "over to you, Priya"). The addressee is the next different speaker.
_HANDOFF_CUES = (
    "hey", "hi", "hello", "over to you", "your turn", "go ahead", "back to you",
    "what do you think", "what about you", "how about you", "let's hear from",
    "tell us", "take it away",
)
# ACKNOWLEDGEMENT cues — thanking/agreeing with the person who JUST spoke
# ("Thanks Ralph", "good point Ralph"). The addressee is the previous speaker.
_ACK_CUES = (
    "thanks", "thank you", "cheers", "good point", "great point", "nice one",
    "well said", "exactly", "agreed", "i agree with",
)

# Capitalised words that look like names but usually aren't — filtered out so the
# heuristics stay precise. (Days/months, pronouns, fillers, common openers.)
_NAME_STOP = {
    "I", "The", "A", "An", "And", "But", "So", "Well", "Yeah", "Yes", "No",
    "Ok", "Okay", "Hi", "Hey", "Hello", "Thanks", "Thank", "Right", "Sorry",
    "Please", "Let", "We", "You", "He", "She", "They", "It", "This", "That",
    "There", "Here", "Now", "Today", "Tomorrow", "Yesterday", "God", "Lord",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
    "Sunday", "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December", "Mr", "Mrs",
    "Ms", "Dr", "Sir", "Everyone", "Everybody", "Guys", "Team", "All", "Good",
    "Maybe", "Actually", "Basically", "Honestly", "Anyway", "Also", "Then",
    "Sure", "Cool", "Nice", "Wait", "Oh", "Hmm", "Yep", "Nope", "Definitely",
    "Absolutely", "Certainly", "Perfect", "Awesome", "Great", "Fine", "True",
    "Exactly", "Indeed", "Alright", "Look", "Listen", "See", "Sounds", "Got",
    "Hold", "Hang", "First", "Second", "Third", "Next", "Finally", "Agreed",
}

_NAME = r"([A-Z][a-z]{1,15})"


def _cue_re(cues):
    return re.compile(r"(?i:\b(?:" + "|".join(re.escape(c) for c in cues)
                      + r"))[\s,]+" + _NAME)


_RE_SELF = re.compile(r"(?i:\b(?:i'?m|i am|this is|my name is|it'?s|here'?s))"
                      r"\s+" + _NAME)
_RE_HANDOFF = _cue_re(_HANDOFF_CUES)
_RE_ACK = _cue_re(_ACK_CUES)
# A name opening a sentence, immediately followed by a comma, is a direct address
# to the next speaker ("Priya, what about marketing?").
_RE_LEAD = re.compile(r"(?:^|[.!?]\s+)" + _NAME + r"\s*,\s")
# A trailing "…, Ralph?" question hands off to the next speaker.
_RE_TRAIL = re.compile(r",\s+" + _NAME + r"\s*\?")


def _candidate_names(text):
    """(handoff, ack, self) name lists for one segment:
      handoff — names addressed to whoever speaks NEXT (questions / hand-overs),
      ack     — names thanking/agreeing with whoever spoke PREVIOUSLY,
      self    — names the speaker calls THEMSELF (a self-introduction)."""
    t = (text or "").strip()
    if not t:
        return [], [], []
    handoff, ack, selfn = [], [], []

    def _add(into, m):
        nm = m.group(1)
        if nm not in _NAME_STOP:
            into.append(nm)

    for m in _RE_SELF.finditer(t):
        _add(selfn, m)
    for m in _RE_HANDOFF.finditer(t):
        _add(handoff, m)
    for m in _RE_LEAD.finditer(t):
        _add(handoff, m)
    for m in _RE_TRAIL.finditer(t):
        _add(handoff, m)
    for m in _RE_ACK.finditer(t):
        _add(ack, m)
    return handoff, ack, selfn


def infer_speaker_names(result):
    """Attach a ranked `suggested_names` list to each speaker in a diarise result,
    inferred from the transcript. Mutates and returns `result` (safe on any shape;
    no-ops when there are no speakers/segments). Idempotent — safe to re-run on a
    stored meeting to refresh suggestions.

    Context window: scans up to 5 turns forward/backward (expanded from the
    original 3) to catch hand-off cues and self-introductions that span longer
    stretches of dialogue. Each name match is assigned a confidence weight:
      • Self-introduction ("I'm Ralph")         → weight 3 (strongest)
      • Hand-off cue ("Hey Ralph, can you…")    → weight 2 (addressee = next)
      • Acknowledgement ("Thanks Ralph")         → weight 2 (addressee = prev)
      • Leading address ("Ralph, what about…")  → weight 2 (hand-off)
      • Trailing question ("…, Ralph?")          → weight 2 (hand-off)
    """
    speakers = (result or {}).get("speakers") or []
    segments = (result or {}).get("segments") or []
    if not speakers or not segments:
        for sp in speakers:
            sp.setdefault("suggested_names", [])
        return result

    # Expanded context window for name inference.
    MAX_NAME_SCAN = 5

    def _other(start, step):
        """The nearest different speaker scanning from `start` in direction
        `step` (within MAX_NAME_SCAN turns) — the likely addressee of a
        hand-off or acknowledgement cue."""
        spk = segments[start].get("speaker")
        j = start + step
        steps = 0
        while 0 <= j < len(segments) and steps < MAX_NAME_SCAN:
            if segments[j].get("speaker") != spk:
                return segments[j].get("speaker")
            j += step
            steps += 1
        return None

    tally = defaultdict(lambda: defaultdict(int))
    for i, seg in enumerate(segments):
        spk = seg.get("speaker")
        handoff, ack, selfn = _candidate_names(seg.get("text", ""))
        for nm in selfn:                       # "I'm Ralph" → THIS speaker
            tally[spk][nm] += 3                 # strongest signal
        if handoff:                            # "Hey Ralph…" → the NEXT speaker
            nxt = _other(i, +1)
            if nxt:
                for nm in handoff:
                    tally[nxt][nm] += 2
        if ack:                                # "Thanks Ralph" → the PREVIOUS speaker
            prev = _other(i, -1)
            if prev:
                for nm in ack:
                    tally[prev][nm] += 2

    for sp in speakers:
        lab = sp.get("label")
        cur = (sp.get("name") or "").strip().lower()
        # Sort by score descending, then alphabetically for stability.
        ranked = sorted(tally.get(lab, {}).items(),
                        key=lambda kv: (-kv[1], kv[0]))
        # Attach up to 3 suggestions.  Format is a list of name strings
        # (backward-compatible with existing callers); confidence scores
        # are stored in a parallel `_name_scores` dict for consumers that
        # want weighted suggestions.
        sp["suggested_names"] = [nm for nm, _sc in ranked if nm.lower() != cur][:3]
        sp["_name_scores"] = {nm: min(sc, 10) for nm, sc in ranked
                              if nm.lower() != cur}
    return result


# ── Helpers ─────────────────────────────────────────────────────────────────

SPEAKER_COLORS = [
    "#D4AF37",  # gold
    "#5AA9E6",  # blue
    "#46C9A8",  # teal
    "#E8825A",  # coral
    "#A855F7",  # purple
    "#E0A92E",  # amber
    "#D86E9A",  # rose
    "#7C91B2",  # steel
]


def _speaker_label(index):
    return f"Speaker {index + 1}"


def _normalize_segments(segments):
    """Normalize segments to [{start_sec, end_sec, text, confidence?}, …]."""
    out = []
    for s in segments:
        entry = {
            "start_sec": float(s.get("start_sec", s.get("start", 0)) or 0),
            "end_sec": float(s.get("end_sec", s.get("end", 0)) or 0),
            "text": str(s.get("text", "") or ""),
            "confidence": float(s.get("confidence", 0.0) or 0.0),
        }
        # Long meetings are decoded in bounded chunks. Their per-segment energy
        # is computed while the owning audio chunk is resident, then preserved
        # here for the one global lightweight speaker-assignment pass.
        if "energy" in s:
            try:
                entry["energy"] = float(s["energy"])
            except (TypeError, ValueError, OverflowError):
                pass
        out.append(entry)
    return sorted(out, key=lambda x: x["start_sec"])


def _merge_turns_and_segments(turns, segments):
    """Overlap pyannote speaker turns with transcription segments.
    Assigns each segment to the speaker whose turn covers the segment's
    midpoint. When a segment spans two speakers, it goes to the one with
    more overlap."""
    out = []
    for seg in segments:
        mid = (seg["start_sec"] + seg["end_sec"]) / 2
        best_speaker = None
        best_overlap = 0
        for turn in turns:
            overlap_start = max(seg["start_sec"], turn["start_sec"])
            overlap_end = min(seg["end_sec"], turn["end_sec"])
            overlap = max(0, overlap_end - overlap_start)
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = turn["speaker"]
        if best_speaker is None:
            best_speaker = "SPEAKER_00"
        out.append({
            "start_sec": seg["start_sec"],
            "end_sec": seg["end_sec"],
            "text": seg["text"],
            "speaker": best_speaker,
        })
    return out

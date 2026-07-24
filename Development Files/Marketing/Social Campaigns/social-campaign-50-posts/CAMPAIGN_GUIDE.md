# Mumble social campaign guide

This folder contains 50 finished static creatives arranged as nine publishing groups for Instagram, X, and TikTok. The artwork is platform-sized, numbered in publishing order, and designed around Mumble's black-and-gold visual system.

## Start here

1. Run `node Source/render.mjs` whenever the campaign data or renderer changes.
2. Review the generated `index.html` gallery and the images in each platform folder.
3. Use `CAPTIONS_AND_ALT_TEXT.md` for the matching caption, qualifier, and alt text.
4. Publish slides in ascending filename order within each folder. Slide 1 is the cover.
5. Add the live Windows download link only after the public destination has been checked.

`render.mjs` generates these campaign-level files:

- `campaign-manifest.json` — structured metadata for automation and QA.
- `campaign-manifest.csv` — spreadsheet-friendly manifest with paths, dimensions, copy, qualifiers, captions, alt text, and file sizes.
- `CAPTIONS_AND_ALT_TEXT.md` — the user-facing copy sheet for all 50 creatives.
- `index.html` — a filterable review gallery.

The editable source is under `Source/`. The rendered PNG files in the platform folders are the publishing masters.

## Folder map

| Carousel | Platform | Folder | Slides | Post IDs | Theme |
|---|---|---|---:|---|---|
| 01 | Instagram | `Instagram/01_Core_Voice/` | 6 | 01–06 | Core dictation, shortcuts, Local/Cloud choices |
| 02 | Instagram | `Instagram/02_Deck_and_Control/` | 6 | 07–12 | Deck, clipboard, presets, Prompt, meetings |
| 03 | Instagram | `Instagram/03_Meetings_and_More/` | 6 | 13–18 | Meeting analysis, Reader, search, stats, download |
| 04 | X | `X/04_Architecture_and_Choice/` | 6 | 19–24 | Data path, provider choice, shortcuts, workflow fit |
| 05 | X | `X/05_Workflows_and_Reuse/` | 6 | 25–30 | Cleanup, presets, multi-item work, exports, Reader |
| 06 | X | `X/06_Beyond_Dictation/` | 5 | 31–35 | History, hardware tiers, privacy, stats, download |
| 07 | TikTok | `TikTok/07_Voice_First/` | 5 | 36–40 | Voice-first drafting and clear privacy lanes |
| 08 | TikTok | `TikTok/08_Deck_and_Meetings/` | 5 | 41–45 | Deck, clipboard, reuse, meetings, Reader |
| 09 | TikTok | `TikTok/09_Search_and_Download/` | 5 | 46–50 | Search, presets, product facts, download |

`Contact Sheets/` is reserved for overview sheets or campaign-review exports. It is not a source folder.

## Master dimensions

| Platform | Master size | Aspect ratio | Total creatives |
|---|---:|---:|---:|
| Instagram | 1080 × 1350 | 4:5 | 18 |
| X | 1920 × 1080 | 16:9 | 17 |
| TikTok | 1080 × 1920 | 9:16 | 15 |

Keep the masters at their rendered size. Do not crop one platform's artwork into another platform's ratio; use the matching platform folder instead.

### TikTok paid-carousel note

Paid TikTok carousel placements require music. Add properly licensed or cleared music during ad setup; the PNG masters themselves are silent. TikTok recommends a vertical minimum of 720 × 1280 for this placement. These masters are higher-resolution 1080 × 1920 files, so they already exceed that recommendation while keeping the intended 9:16 composition. Recheck placement rules and safe-area previews in TikTok Ads Manager immediately before launch, as platform requirements can change.

## Recommended publishing order

Publish every folder in numeric order, and keep every folder's slides in ascending filename order.

For a coordinated cross-platform campaign, use three waves:

1. **Core awareness:** Carousel 01 on Instagram, 04 on X, and 07 on TikTok.
2. **Workflow consideration:** Carousel 02 on Instagram, 05 on X, and 08 on TikTok.
3. **Feature breadth and conversion:** Carousel 03 on Instagram, 06 on X, and 09 on TikTok.

Within one platform, the order is therefore 01 → 02 → 03 for Instagram, 04 → 05 → 06 for X, and 07 → 08 → 09 for TikTok. Stagger platforms if desired, but keep each wave's message together.

The folders are publishing groups. Use them as native carousels or multi-image posts where the selected placement supports the full slide count. If a placement accepts fewer images, split the group into an ordered thread or series without changing the slide sequence.

## Truthful claim guardrails

Keep these rules intact when editing artwork, captions, replies, landing-page copy, or paid-ad variants:

- **Windows is the current product.** Do not imply that macOS or Linux downloads are available now.
- **Local transcription is the default.** In Local mode, speech transcription runs on-device. Cloud transcription is optional and sends audio to the selected provider.
- **Describe the specific data path.** Cloud AI polish receives transcript text; Cloud transcription separately receives audio. Avoid blanket claims such as “nothing ever leaves your PC,” “always local,” or “100% private.”
- **Do not call the whole product fully offline.** Core dictation can work offline when Local transcription is selected and cloud AI is not being used. Reader voice and optional cloud services are separate.
- **Use focused-field language.** Say “at your cursor,” “in the focused text field,” or “across common Windows workflows.” Do not promise support in every app without exception.
- **Smart Modes and presets are deliberate actions.** Do not claim that Mumble automatically infers every email, list, or reply.
- **The current preset count is 20 built-ins plus 5 custom slots.** Do not reuse the older 17 + 3 figure.
- **Meeting capture means microphone audio or an imported audio file.** Do not claim native system-audio, Zoom, or Teams capture.
- **Use “speaker-labelled segments.”** Do not promise reliable speaker identity or perfect diarisation. Mumble's default speaker labels are heuristic.
- **Meeting recordings and imports are capped at four hours.** Meeting summaries, action items, decisions, and open questions are optional AI analysis of the transcript.
- **Reader opens 11 supported formats.** Voice playback sends short passages to the configured TTS provider. Document summarisation sends text to the configured AI provider.
- **Time saved is an estimate.** Do not present it as directly measured time or invent example savings, accuracy rates, speed multipliers, customer counts, or testimonials.
- **“Free” refers to the Windows download and Local-mode starting path.** Optional providers can charge for transcription, TTS, or AI usage.
- **Use “MIT-licensed” as the factual licence statement.** Do not add “inspect the source,” repository, GitHub, fork, or star CTAs unless a public repository and approved URL actually exist.

## Provider qualifiers

Some creatives include a `qualifier` in `campaign-data.js`. `render.mjs` carries it into both manifest files and `CAPTIONS_AND_ALT_TEXT.md`. Do not delete a qualifier when publishing an AI-, cloud-, or TTS-dependent claim.

Approved wording includes:

- **Cloud AI:** “Requires a configured AI provider; provider charges may apply.”
- **Smart Modes/presets:** “AI transformations require a configured provider or compatible local model; provider charges may apply.”
- **Cloud transcription:** “Cloud transcription requires a configured provider; provider charges may apply.”
- **Reader:** “Voice playback requires a configured TTS provider; provider charges may apply.”
- **Meeting analysis:** “Meeting analysis requires a configured AI provider; provider charges may apply.”

If a qualified slide is used by itself, keep the qualifier with that slide. If it appears inside a carousel, include the relevant qualifier in the main post caption or the platform's disclosure field. Keep it readable; do not hide it through tiny text or low contrast.

## No-living-creatures policy

This campaign must contain no humans, mascots, animals, or other living creatures, including AI-generated versions.

Use only product UI, abstract interface elements, typography, waveforms, keycaps, cursors, documents, charts, locks, devices, geometric symbols, and non-figurative icons. Do not add faces, hands, bodies, human silhouettes, avatars, animals, birds, insects, or character-like mascots. For meeting visuals, use neutral labels such as “Speaker 1” and “Speaker 2” without profile pictures or person icons.

Check any replacement asset against this policy before rendering or publishing.

## Captions and links

- Copy the matching text from `CAPTIONS_AND_ALT_TEXT.md`; do not guess from the image alone.
- For an individual creative, use its matching caption and qualifier.
- For a full carousel, use the first slide's caption as the base, add one concise campaign CTA, and include any provider qualifier required by later slides.
- Use one canonical Windows download or landing-page URL. Confirm that it resolves correctly before launch.
- Add consistent campaign UTM parameters in the publishing tool, not as visible text on the artwork.
- Instagram feed-caption links are not reliably clickable; use the approved profile link and “link in bio” wording.
- On TikTok, use the approved profile or ad-destination link. Do not place a long raw URL on the artwork.
- X captions in the source are written below the 280-character limit before a link is added. Recount the final post after adding the URL or disclosure text.
- Do not add a repository link or source-inspection CTA unless it has been explicitly approved and published.

## Accessibility and alt text

Every creative has purpose-written alt text in `CAPTIONS_AND_ALT_TEXT.md` and both manifests. Add alt text to each image separately when the platform supports it; a six-slide carousel needs six image descriptions.

Keep the supplied alt text aligned with the final artwork. If the artwork changes, update the alt text before re-rendering. Preserve readable contrast, do not communicate meaning through colour alone, and keep labels such as Local, Cloud, Prompt, or Speaker visible alongside their colour treatment.

## Final pre-publish check

- Correct platform folder and dimensions.
- Slides uploaded in numeric order.
- Caption and alt text match the selected files.
- Required provider qualifier is present.
- Windows-only availability is clear.
- No absolute local/offline/any-app claim has been introduced.
- No invented metric or performance claim appears.
- No human, mascot, animal, avatar, or living creature appears.
- Download link works and points to the approved public destination.
- TikTok paid carousel has licensed/cleared music and passes the placement preview.

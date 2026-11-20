# AI voices

The **Voices** dock gives the characters of Ratchet & Clank and Going Commando new lines in their
own voices. An open AI voice model learns each voice from your own disc, then says any words you
type. Everything runs on your computer. Nothing from the disc is in this repository or leaves your
computer.

## The four steps

Open a level from the launcher (Mods, Level editor, Open). The dock is on the right.

1. **Model.** The dock shows your graphics card and its preset, and the models ranked for it. The
   green one is the best that runs here. Pick one, press **Use this model** (it installs itself the
   first time), and **Install listening** once.
2. **Characters.** **Read the disc** finds every spoken line. **Listen** writes down the lines
   without subtitles and groups the lines by voice. Click a voice to hear it, type who it is
   (Clank) and press **Name**. A name also reaches the same lines in the disc's other languages.
   Voices with only a line or two are folded at the bottom; name those in Lines, one by one.
3. **Lines.** Pick a character, click a line, change its words if you like, and press **Speak
   it**. Each press makes a new take; the list under the words keeps them all, and the one picked
   there is the one the game plays (or the disc's own line). **Speak every line shown** does a
   character's whole part.
4. **Cutscene.** Select a subtitle (a CutsceneSubtitle) of a cutscene you made in the editor, pick
   whose voice says it and press **Speak this subtitle**. The dock writes the voice line next to the
   subtitle as a CutsceneSound. An actor whose rig has a jaw moves its mouth with the words.

**Put the voices in your mods** makes the *AI voices* pack. Switch it on in the launcher's Mods
page and play with your edits.

## What changes in the game

- A take replaces the line inside its recording on the disc, by the recording's key (OpenRAC's
  `extractor/audio.py`, `stream_key`). A scene's recording keeps its length. The take goes where the
  old line was and is cut before the next line, so the scene stays in time. The dock tells you
  when a take was too long.
- Subtitles and help text keep the disc's words. The new words are spoken only.
- In the game's own scenes, the mouths keep the original animation. In editor cutscenes, the jaw
  follows the take (`face.talk`, also on actors that play a game scene's pose).
- Going Commando's video scenes (PSS files) cannot be changed.

## Models (November 2026)

Each preset lists its models best first. The dock starts on the first one that runs on this
computer and speaks every language of your disc. Any model can still be picked.

| Preset | Graphics memory | Models, best first |
|---|---|---|
| No graphics card | none | Chatterbox Multilingual, ZONOS3-Mini |
| Light | 4 to 7 GB | Chatterbox Multilingual, Qwen3-TTS 0.6B, ZONOS3-Mini |
| Medium | 8 to 11 GB | VoxCPM2, Qwen3-TTS 1.7B, Chatterbox Multilingual, ZONOS3-Mini |
| Strong | 12 to 23 GB | VoxCPM2, Fish Audio S2 Pro, Qwen3-TTS 1.7B, Chatterbox Multilingual, ZONOS3-Mini |
| Very strong | 24 GB and more | Voxtral TTS 4B, Fish Audio S2 Pro, VoxCPM2, Qwen3-TTS 1.7B, Chatterbox Multilingual |
| Apple Silicon | shared memory | Chatterbox Multilingual, ZONOS3-Mini, VoxCPM2 |

| Model | Licence | Card memory | Download | Languages | Runs |
|---|---|---|---|---|---|
| Chatterbox Multilingual | MIT | 3.5 GB | 3.2 GB | 23 | in the editor, also on the processor and Macs |
| Qwen3-TTS 0.6B | Apache 2.0 | 3 GB | 2 GB | 10 (no Polish) | in the editor |
| Qwen3-TTS 1.7B | Apache 2.0 | 6 GB | 4.2 GB | 10 (no Polish) | in the editor |
| VoxCPM2 | Apache 2.0 | 8 GB | 5 GB | 26 | in the editor |
| ZONOS3-Mini | Apache 2.0 | any | 2 GB | 25 | its own server |
| Fish Audio S2 Pro | research, no commercial use | 16 GB | 9.5 GB | 25 | its own server |
| Voxtral TTS 4B | CC BY-NC 4.0 | 16 GB | 8.5 GB | 9 (no Japanese) | its own server |

The catalogue is `openrac/tools/voices/models.json`. Left out: IndexTTS-2.5 (its licence, and no
French, German or Italian), Breeze TTS 2 (English and Chinese only), XTTS v2 (old).

OpenRAC is not sold, so the non-commercial licences are fine for it. A pack made with them must
not be sold either.

**Server models** run in their own program, started as their page says. The dock shows the
address it connects to; change it there if yours is another.

## Where things are

All of it is in `voices/` in the launcher's data folder. Delete it to start again.

```
voices/
  envs/<model>/         one Python environment per model (made with uv)
  envs/listen/          Whisper and the speaker model
  models/               the models' weights
  <game>-<version>/     a disc's lines, names, words and takes
```

Each model gets its own environment because they need different versions of the same packages.
The dock uses `uv` (fetched once, checked against its published checksum) to make them, with the
PyTorch build for your card.

## For developers

- `openrac/tools/voice_lab.py`: the commands, from a shell or for the dock (`serve`).
- `openrac/tools/voices/`: hardware, models, environments, the worker that runs inside an
  environment, the disc's speech, voices, the workspace and the pack.
- `openrac/project/addons/openrac_library/voices_dock.gd` and `voices_lab.gd`: the dock.
- Tests: `test_voices.py` (parts), `test_voices_e2e.py` (the processes, with local HTTP servers in
  place of model servers), `test_voices_dock.py` (the dock in Godot, with `OPENRAC_GODOT`). CI runs
  them on Linux, Windows and macOS.

```
python3 openrac/tools/voice_lab.py --data DATA hardware
python3 openrac/tools/voice_lab.py --data DATA read DISC.iso
python3 openrac/tools/voice_lab.py --data DATA --game rac1 --version ntsc listen --languages en
python3 openrac/tools/voice_lab.py --data DATA --game rac1 --version ntsc speak --model chatterbox-multilingual --line KEY:0
```

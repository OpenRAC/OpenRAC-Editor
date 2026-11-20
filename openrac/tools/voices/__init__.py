"""AI voices for the game's characters, made on the player's own computer.

The editor's Voices dock drives this package through voice_lab.py. It reads the speech of the
player's own disc (catalogue), groups it by who speaks (speakers), lets a voice model of the
player's choice speak new text in a character's voice (models, envs, worker), and writes what
was made as a sound pack the game plays (pack). Nothing here ships recordings or model weights:
the samples come from the player's disc, and the model is installed when the player asks.

The parts run by the launcher's Python use the standard library only. The worker runs inside the
model's own environment (envs), where the model's packages are.
"""

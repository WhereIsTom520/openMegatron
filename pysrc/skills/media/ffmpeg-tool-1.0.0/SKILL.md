---
name: ffmpeg_tool
description: Common video/audio editing operations via FFmpeg — trim, concat, add subtitles, add audio, convert format, extract frames, extract audio, probe metadata.
category: media
entry_function: main
parameters:
  type: object
  properties:
    action:
      type: string
      description: >
        Operation to perform. One of:
        - info (probe video metadata)
        - trim (cut a segment)
        - concat (join multiple videos)
        - add_subtitles (burn subtitles into video)
        - add_audio (replace or overlay audio track)
        - convert (format/codec conversion)
        - extract_frames (export frames as images)
        - extract_audio (dump audio track to file)
    input:
      type: string
      description: Path to the input video/audio file (or first input for concat).
    output:
      type: string
      description: Path for the output file.
    ss:
      type: string
      description: Start time for trim (e.g., "00:01:30" or "90").
    t:
      type: string
      description: Duration for trim (e.g., "00:00:10" or "10").
    to:
      type: string
      description: End time for trim (alternative to t).
    inputs:
      type: array
      items:
        type: string
      description: List of input file paths for concat operation.
    subtitle_file:
      type: string
      description: Path to subtitle file (.srt/.ass) for add_subtitles.
    audio_file:
      type: string
      description: Path to audio file for add_audio.
    audio_replace:
      type: boolean
      description: If true, replace original audio; otherwise mix (default false).
    audio_volume:
      type: number
      description: Volume for mixed audio, e.g. 0.3 (default 1.0).
    format:
      type: string
      description: Target format for convert (e.g., "mp4", "gif", "webm", "mov").
    vcodec:
      type: string
      description: Video codec for convert (e.g., "libx264", "libx265").
    acodec:
      type: string
      description: Audio codec for convert (e.g., "aac", "mp3", "copy").
    width:
      type: integer
      description: Output width in pixels for resize during convert.
    height:
      type: integer
      description: Output height in pixels for resize during convert.
    fps:
      type: number
      description: Frames per second for extract_frames, or output fps for convert.
    max_frames:
      type: integer
      description: Maximum number of frames to extract.
    crf:
      type: integer
      description: Quality for convert (18-28, lower=better, default 23).
    bitrate:
      type: string
      description: Target bitrate for convert (e.g., "2M").
    overwrite:
      type: boolean
      description: Overwrite output if exists (default false).
  required:
    - action
keywords: [ffmpeg, video, audio, trim, concat, subtitle, convert, edit, media]
produces:
  stdout: JSON with status, action, file paths
side_effects:
  - Creates or overwrites output files in the filesystem.
risk: medium
---

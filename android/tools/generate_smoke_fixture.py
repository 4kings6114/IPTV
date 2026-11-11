"""Generate our own tiny AVC/AAC test media; no provider content or runtime asset."""
from pathlib import Path
import shutil
import subprocess

output = Path(__file__).resolve().parents[1] / 'app/src/androidTest/assets/stability.mp4'
output.parent.mkdir(parents=True, exist_ok=True)
if not shutil.which('ffmpeg'):
    raise RuntimeError('The CI test runner must install ffmpeg, never bundle it in the app')
subprocess.run([
    'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
    '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=8',
    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100',
    '-t', '105', '-c:v', 'libx264', '-preset', 'ultrafast', '-profile:v', 'baseline',
    '-pix_fmt', 'yuv420p', '-b:v', '48k', '-c:a', 'aac', '-b:a', '24k', '-ac', '1',
    '-movflags', '+faststart', str(output),
], check=True, timeout=90)
assert 1000 < output.stat().st_size < 4_000_000
print('Generated synthetic AVC/AAC Android-test-only media:', output.stat().st_size, 'bytes')

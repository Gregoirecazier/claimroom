"""Build timestamped visual inputs from the versioned synthetic video library."""
import hashlib
import json
import subprocess
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
import imageio_ffmpeg

root = Path(__file__).resolve().parents[1] / 'claim_api' / 'fixture_media'
entries = []
for index, video in enumerate(sorted(root.glob('g*/*.mp4')), 1):
    identifier = str(uuid5(NAMESPACE_URL, f'claimroom:video-catalogue:v1:{index}'))
    folder = root / 'catalogue' / identifier
    folder.mkdir(parents=True, exist_ok=True)
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-y',
        '-i', str(video), '-vf', 'fps=4,scale=1280:-2', '-q:v', '3', str(folder / '%03d.jpg')], check=True)
    frames = [{'path': str(p.relative_to(root)), 'seconds': (i + .5) / 4}
              for i, p in enumerate(sorted(folder.glob('*.jpg')))]
    entries.append({'id': identifier, 'label': f'Archive vidéo {index:02}',
        'path': str(video.relative_to(root)), 'sha256': hashlib.sha256(video.read_bytes()).hexdigest(),
        'frames': frames, 'synthetic': True})
(root / 'catalogue' / 'manifest.json').write_text(json.dumps(entries, indent=2) + '\n')
print([(e['label'], len(e['frames'])) for e in entries])

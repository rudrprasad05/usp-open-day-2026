# DrawAI

DrawAI is a local, web-based Open Day game. One visitor draws on a phone, the drawing appears live on a laptop, and an OpenCLIP model continuously guesses the object. The phone alone sees the secret word until the round ends.

## How it works

- **FastAPI** serves the phone and display pages and owns the game state.
- **WebSockets** send normalized stroke segments, controls, predictions, and round state in real time.
- **OpenCLIP** compares each drawing with cached text embeddings for all 25 labels. The target is never passed to the predictor.
- **Pillow** redraws the stroke history as a centered black-on-white square for inference.
- Inference is throttled (650 ms by default), runs outside the async event loop, and never queues one job per stroke.

The percentages are relative confidence scores across the configured labels, not calibrated statistical probabilities.

## Requirements

- Python 3.11 or 3.12 recommended (PyTorch wheels may not yet support newer Python releases)
- A laptop and phone on the same Wi-Fi/LAN
- Internet for the first dependency install and model download only

## Fedora setup

```bash
sudo dnf install python3.12 python3.12-devel
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If `python3.12` is not available under that exact name, use a supported Python from Fedora's repositories or `pyenv`. A CPU-only PyTorch installation saves disk space; use the current command from the official PyTorch installer, then install this requirements file.

## First model download

The first server start downloads the configured OpenCLIP weights to the normal Torch/Hugging Face cache. It can take several minutes:

```bash
./run.sh
```

Wait for `OpenCLIP predictor ready` in the log. Keep the cache directory intact. After dependencies and weights are cached, DrawAI can run without internet access. The HTML uses system-font fallbacks offline; all application JavaScript and CSS is local.

## Run the demonstration

```bash
source .venv/bin/activate
./run.sh
```

Open `http://localhost:8000/display` on the laptop. Find the LAN address on Fedora with:

```bash
ip -br address
```

Look for the Wi-Fi interface address, then open `http://LAPTOP_LAN_IP:8000/draw` on the phone. Fedora's firewall may need the port opened:

```bash
sudo firewall-cmd --add-port=8000/tcp
sudo firewall-cmd --runtime-to-permanent
```

For a QR code that always uses the correct address, include the laptop address when starting:

```bash
PUBLIC_HOST=192.168.1.50 ./run.sh
```

`PUBLIC_HOST` may also be a full origin such as `http://192.168.1.50:8000`. The QR code is optional; direct navigation always works.

## Configuration

Environment variables keep common changes out of the code:

| Variable | Default | Purpose |
| --- | --- | --- |
| `GAME_DURATION_SECONDS` | `30` | Round duration |
| `WIN_CONFIDENCE_THRESHOLD` | `0.55` | Minimum top-score needed for an AI win |
| `PREDICTION_INTERVAL_SECONDS` | `0.65` | Minimum inference interval |
| `OPENCLIP_MODEL` | `ViT-B-32` | OpenCLIP model architecture |
| `OPENCLIP_PRETRAINED` | `laion2b_s34b_b79k` | Pretrained weights identifier |
| `PUBLIC_HOST` | request host | QR-code host or origin |
| `PORT` | `8000` | Server port |

Example: `GAME_DURATION_SECONDS=45 WIN_CONFIDENCE_THRESHOLD=0.45 ./run.sh`.

Edit `app/labels.py` to change the object list. Restart the server so the model can rebuild and cache the text embeddings.

## Reliability notes

The server keeps stroke paths, predictions, target, timing, and result in memory, so either browser can refresh and recover the current round. WebSockets reconnect automatically. Undo removes a complete pointer path. A model load failure does not stop drawing or synchronization; the display reports that AI is unavailable and the server logs the cause.

The game state is intentionally ephemeral and resets when the server restarts. Run one server process—multiple worker processes would each have separate in-memory games.

## Raspberry Pi 5 considerations

The predictor is isolated in `app/predictor.py`, making it replaceable without changing the game or WebSocket code. The default ViT-B/32 model may be slow and memory-heavy on a Pi 5. Before an event, test CPU latency and consider a smaller OpenCLIP-compatible model, lower inference frequency, or an ONNX/TFLite replacement behind the same `predict(image)` interface. No Pi-specific optimization is included in this laptop-first version.


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

- Python 3.11 or newer with compatible PyTorch and torchvision wheels (the current CPU wheels also support Python 3.14)
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

If `python3.12` is not available under that exact name, use a supported Python from Fedora's repositories or `pyenv`. A CPU-only PyTorch installation saves disk space. To install CPU-only wheels before the rest of the requirements, use:

```bash
pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision
pip install -r requirements.txt
```

## First model download

The first server start downloads the configured OpenCLIP weights to the normal Torch/Hugging Face cache. It can take several minutes:

```bash
./run.sh
```

Wait for `[AI] Predictor ready` in the log. If startup reports `[AI ERROR]`, inference is unavailable (drawing still works); read the full traceback and check dependencies, connectivity on first download, and cached weights. With the current OpenCLIP release, the configured ViT-B/32 weights were cached under `~/.cache/huggingface/hub/`; other pretrained tags may use `~/.cache/torch/hub/checkpoints/`. Keep the cache directory intact. After dependencies and weights are cached, DrawAI can run without internet access. The HTML uses system-font fallbacks offline; all application JavaScript and CSS is local.

For an explicitly offline startup that never checks the model host, use `HF_HUB_OFFLINE=1 ./run.sh` after the first successful download. This was verified with the default ViT-B/32 weights.

## Run the demonstration

```bash
source .venv/bin/activate
./run.sh
```

`run.sh` also selects the project’s `.venv` automatically when it exists. This prevents a system Python with missing AI packages from quietly running the drawing UI without a model.

Open `http://localhost:8888/display` on the laptop (`run.sh` defaults to port 8888). Find the LAN address on Fedora with:

```bash
ip -br address
```

Look for the Wi-Fi interface address, then open `http://LAPTOP_LAN_IP:8888/draw` on the phone. Fedora's firewall may need the port opened:

```bash
sudo firewall-cmd --add-port=8888/tcp
sudo firewall-cmd --runtime-to-permanent
```

For a QR code that always uses the correct address, include the laptop address when starting:

```bash
PUBLIC_HOST=192.168.1.50 ./run.sh
```

`PUBLIC_HOST` may also be a full origin such as `http://192.168.1.50:8888`. The QR code is optional; direct navigation always works.

## Configuration

Environment variables keep common changes out of the code:

| Variable | Default | Purpose |
| --- | --- | --- |
| `GAME_DURATION_SECONDS` | `30` | Round duration |
| `WIN_CONFIDENCE_THRESHOLD` | `0.55` | Minimum top-score needed for an AI win |
| `PREDICTION_INTERVAL_SECONDS` | `0.65` | Minimum inference interval |
| `PREDICTION_LOGIT_SCALE` | `25` | Softmax scaling for relative confidence; larger values make guesses more peaked |
| `OPENCLIP_MODEL` | `ViT-B-32` | OpenCLIP model architecture |
| `OPENCLIP_PRETRAINED` | `laion2b_s34b_b79k` | Pretrained weights identifier |
| `PUBLIC_HOST` | request host | QR-code host or origin |
| `PORT` | `8888` in `run.sh` | Server port |
| `SAVE_DEBUG_PREDICTION_IMAGES` | `false` | Overwrite `debug/prediction-latest.png` with the square image sent to the model |

Example: `GAME_DURATION_SECONDS=45 WIN_CONFIDENCE_THRESHOLD=0.45 ./run.sh`.

Edit `app/labels.py` to change the object list. Restart the server so the model can rebuild and cache the text embeddings.

## Check the prediction pipeline

To smoke-test the real model independently of the browser or WebSocket, run:

```bash
python -m app.predictor_test
```

This verifies that the pretrained model loads, text embeddings have 25 rows, image preprocessing and inference complete, all 25 scores are finite and sum to one, and a top five exists. It does not require one particular answer for its sample sketch.

During a round, `POST /api/debug/predict-current` directly classifies the current server-side drawing and returns its stroke count and top five. For example:

```bash
curl -X POST http://localhost:8888/api/debug/predict-current
```

It returns HTTP 422 if there is too little ink, 503 if the model failed to load, or 500 with an error if inference fails. Enable `SAVE_DEBUG_PREDICTION_IMAGES=true` while diagnosing image quality; the file is overwritten after each prediction, not accumulated. The server logs prediction requests, segment counts, inference time, guesses, broadcasts, and full tracebacks for failures. Guesses are broadcast regardless of the winning threshold; that threshold controls only the `AI GOT IT!` outcome.

## Reliability notes

The server keeps stroke paths, predictions, target, timing, and result in memory, so either browser can refresh and recover the current round. WebSockets reconnect automatically. Undo removes a complete pointer path. A model load failure does not stop drawing or synchronization; the display reports that AI is unavailable and the server logs the cause.

The game state is intentionally ephemeral and resets when the server restarts. Run one server process—multiple worker processes would each have separate in-memory games.

## Raspberry Pi 5 considerations

The predictor is isolated in `app/predictor.py`, making it replaceable without changing the game or WebSocket code. The default ViT-B/32 model may be slow and memory-heavy on a Pi 5. Before an event, test CPU latency and consider a smaller OpenCLIP-compatible model, lower inference frequency, or an ONNX/TFLite replacement behind the same `predict(image)` interface. No Pi-specific optimization is included in this laptop-first version.

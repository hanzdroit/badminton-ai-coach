# Badminton AI Coach — Prototype 02

Phone-only live pose tracking prototype.

## Run it
1. Upload `index.html` to a GitHub repository.
2. In the repository, open **Settings → Pages**.
3. Choose **Deploy from a branch**, select `main` and `/root`, then Save.
4. Open the published HTTPS link on your phone (Chrome recommended).
5. Allow camera access, use landscape mode, and tap **Enable camera**.
6. Wait for the pose model to load, then tap **Start training**.

You can also serve the file locally from a computer over HTTPS, but opening the HTML directly as a file on a phone will not grant camera access.

## What it does
- Uses the phone rear camera.
- Runs MediaPipe Pose Landmarker in the browser.
- Draws a live skeleton overlay.
- Estimates movement from the midpoint of the hips.
- Lets you start/stop a session and export hip-position samples as CSV.

## Important limitations
- This is an early proof of concept, not a badminton-specific motion model.
- It tracks one prominent pose, not a chosen player among four doubles players.
- The movement index is relative screen movement, not court distance, speed, agility or a validated score.
- It does not detect shots, shuttle trajectory, smash speed, wins/losses or weaknesses yet.
- At long camera distances, pose tracking may fail if the player occupies too few pixels.
- The page downloads the MediaPipe runtime and pose model from public CDNs/Google model hosting. Inference is performed locally in the browser; video frames are not sent to an AI server by this app.
- Camera access requires HTTPS (or localhost).

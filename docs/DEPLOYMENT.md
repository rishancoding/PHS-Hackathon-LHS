# Publish PhotoGrid

The repository includes a Render Blueprint for the full Python app. It has not
been deployed yet; creating the public service requires your Render/GitHub login.

1. Open [Deploy PhotoGrid on Render](https://render.com/deploy?repo=https://github.com/rishancoding/PHS-Hackathon-LHS).
2. Sign in, connect this GitHub repository, and deploy the Blueprint from **main**.
   Keep the service name **photogrid** and the **Free** plan from `render.yaml`.
3. After the build succeeds, open the service's assigned `onrender.com` URL.
   Render assigns the final hostname; it may include a suffix. Keep `photogrid`
   in the service name. The app also works at `/photogrid` on that hostname.
4. Click **Try the sample area** to test roof detection, panel previews, no-panel
   zones, model comparisons and the PDF without Google requests.

The Blueprint installs `requirements-hosted.txt` and runs Gunicorn with one
worker and four threads. One worker is required because job state is held in
memory. The app accepts Render's assigned hostname, handles HTTPS forwarded by
Render, and still rejects POST requests from other origins. `/health` is its
health-check endpoint.

## Keys and saved searches

Your local `.env` stays ignored and is not uploaded. The Blueprint leaves
`GOOGLE_MAPS_API_KEY` empty. A visitor who wants a live search must supply a Google
key in the existing input; it is used for that request and excluded from saved
results and reports. Do not put your personal key into the shared public demo.

To enable saved projects on Render, set **MONGODB_URI** as a secret environment
variable and **MONGODB_DATABASE** to `photogrid`, and allow the service's outbound
IP addresses in Atlas. Keep the URI out of the Blueprint and Git. Atlas retains
planning snapshots across redeploys; aerial files still depend on the service's
file storage. This is a shared demo, so visitors can see the saved projects.

To enable the solar advisor on Render, set **DEEPSEEK_API_KEY** as a secret
environment variable; keep it out of the Blueprint and Git. Visitors' questions are
billed to that DeepSeek account, and the server allows 20 questions a minute across
all visitors.

This is a shared hackathon demo: saved searches and reports are accessible to
other visitors. Use the sample area or public addresses. The page shows this
notice when hosted. Free hosting has ephemeral files; saved imagery and searches
can disappear after restarts or redeploys. It also sleeps when idle, so open it
before judging and keep a local demo and downloaded PDF ready. Larger live
searches can exceed the free service's memory; the sample is the best first check.

Provider references: [Flask deployment](https://render.com/docs/deploy-flask),
[Blueprint configuration](https://render.com/docs/blueprint-spec),
[free service limitations](https://render.com/docs/free), and
[Render environment variables](https://render.com/docs/environment-variables).

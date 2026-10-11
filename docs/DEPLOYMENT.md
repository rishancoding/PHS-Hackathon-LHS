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

Your local `.env` stays ignored and is not uploaded.

When you first deploy the Blueprint, Render asks for **GOOGLE_MAPS_API_KEY** (live
address searches), **DEEPSEEK_API_KEY** (solar advisor) and **MONGODB_URI** (saved
projects). `render.yaml` marks all three `sync: false`, so their values stay in
Render and never enter the Blueprint or Git; it sets `MONGODB_DATABASE=photogrid`
for you.

With a Google key on Render, the site hides its key box and every visitor's search
is billed to that key, so protect it in Google Cloud: restrict it to the Geocoding,
Solar and Places (New) APIs, and set daily quotas and a budget alert. Without one,
the site shows the box and a visitor's own key is used only for that request,
never saved in results or reports. Render only asks during that first
creation: for a Blueprint that already exists, add or change these keys on the
`photogrid` service's **Environment** page.

For saved projects, also allow the service's outbound IP addresses in Atlas. Atlas
retains planning snapshots across redeploys; aerial files still depend on the
service's file storage. This is a shared demo, so visitors can see the saved projects.

Advisor questions are billed to the DeepSeek account behind the key, and the server
allows 20 questions a minute across all visitors.

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

## Use a Vercel public address

Import this GitHub repository into Vercel with **Root Directory = `vercel`**.
The [Vercel setup guide](../vercel/README.md) explains the required
`PHOTOGRID_BACKEND_URL` setting and sample checks. Deploy the Render service
above first: Vercel serves as the website gateway, while Render runs the
background analysis and retains working raster files. Importing the repository
root directly as a Vercel Python Function is not supported by this app's
in-memory job and local imagery architecture.

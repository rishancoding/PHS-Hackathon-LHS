# PhotoGrid on Vercel

This folder is the Vercel project root. It provides the public website address
and forwards the existing dashboard, assets and API requests to the Python
service. Analysis threads, raster files and MongoDB connections stay on that
service; Vercel instances do not need shared job state or geospatial packages.

1. Deploy the repository's Render Blueprint first, as described in
   [the deployment guide](../docs/DEPLOYMENT.md). Verify its `/health` endpoint
   and run **Try the sample area** on the Render URL.
2. In Vercel, import **rishancoding/PHS-Hackathon-LHS**, branch **main**.
3. Set **Root Directory** to `vercel`. Keep the detected **Flask** framework.
   Leave build command and output directory at their defaults.
4. Add **PHOTOGRID_BACKEND_URL** to Production (and Preview if desired): your
   actual Render origin, for example `https://photogrid-example.onrender.com`.
   Do not include `/photogrid`. Set the Vercel project name to `photogrid` or
   another available name containing it, then deploy.
5. Open the assigned Vercel URL and try the sample, model previews, applied
   no-panel zones, PDF download and Save/Open project.

For a custom domain or additional alias, set **PHOTOGRID_PUBLIC_URL** to its
HTTPS origin and redeploy. Vercel's generated deployment and production domains
are accepted automatically using its system environment variables. Keep the
setting that exposes Vercel system environment variables enabled.

Google, DeepSeek and MongoDB credentials belong in Render's secret environment settings,
not Vercel, GitHub, or this folder. Set `MONGODB_DATABASE=photogrid` on Render and
allow its outbound IPs in Atlas. Leave the Google key unset for a sample-only
public demo, or use the existing visitor key input.

The gateway checks browser POST origins before forwarding them with the backend
origin. It keeps the browser on the Vercel domain and needs no CORS changes.
Keep Fluid Compute enabled (the default). Its 300-second request limit applies to individual API calls; analyses run on
Render and the dashboard continues polling. Large raw TIFF downloads go
directly to Render to avoid Vercel's response size limit. Other responses above
4 MB show an error; they remain accessible directly on the Render site.
Render's existing cold starts, memory limits and ephemeral imagery storage
still apply. Atlas retains saved planning snapshots, but not raster files.

This is deployment preparation, not an already hosted website. Both services
require deployment through your accounts. GitHub pushes automatically redeploy
the connected services once configured.

References: [Vercel Flask](https://vercel.com/docs/frameworks/backend/flask),
[Vercel function limits](https://vercel.com/docs/functions/limitations),
[Vercel system environment variables](https://vercel.com/docs/environment-variables/system-environment-variables).

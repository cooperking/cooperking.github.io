# Grifflix: the Android app

A Trusted Web Activity of https://cooperking.github.io/grifflix/ (the site in Chrome, full screen, its notifications as
the app's), made by Bubblewrap's library (`make.js`) and built by `.github/workflows/grifflix-android.yml`, signed with an
upload key made the same every time from YT_CLIENT_SECRET (`make_key.py`). Package io.github.cooperking.grifflix.app
(the Fire TV app, _grifflix/tvapp, is io.github.cooperking.grifflix). Each build puts:
- grifflix/android.apk on the site, to install directly (Android asks to allow installing from Chrome once);
- the .aab for Google Play as the run's artifact;
- its line in .well-known/assetlinks.json (beside the songs' app's), so Chrome opens it full screen.
Once on Google Play: Play's app signing key fingerprint into play-fingerprints.txt, and run the workflow again.

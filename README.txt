Essentia model outputs for the John's Song of the Day previews

Everything the models returned, unchanged. Audio: each song's 30-second Spotify preview (96 kbps MP3),
loaded with MonoLoader(sampleRate=16000, resampleQuality=4). Essentia: essentia-tensorflow 2.1b6.dev1389.

Embedding models:
  https://essentia.upf.edu/models/feature-extractors/discogs-effnet/discogs-effnet-bs64-1.pb  TensorflowPredictEffnetDiscogs(output="PartitionedCall:1"), default patches:
    128 frames (about 2 s) every 62 frames (about 1 s)
  https://essentia.upf.edu/models/feature-extractors/musicnn/msd-musicnn-1.pb  TensorflowPredictMusiCNN(output="model/dense/BiasAdd"), default patches:
    187 frames (about 3 s) every 93 frames (about 1.5 s)

Output models (file in per-patch/, classes in the order the model returns them):
  mtg_jamendo_moodtheme-discogs-effnet-1  reads effnet; TensorflowPredict2D(input="model/Placeholder", output="model/Sigmoid"); 56 outputs
    https://essentia.upf.edu/models/classification-heads/mtg_jamendo_moodtheme/mtg_jamendo_moodtheme-discogs-effnet-1.pb
  genre_discogs400-discogs-effnet-1  reads effnet; TensorflowPredict2D(input="serving_default_model_Placeholder", output="PartitionedCall:0"); 400 outputs
    https://essentia.upf.edu/models/classification-heads/genre_discogs400/genre_discogs400-discogs-effnet-1.pb
  mood_happy-discogs-effnet-1  reads effnet; TensorflowPredict2D(input="model/Placeholder", output="model/Softmax"); 2 outputs
    https://essentia.upf.edu/models/classification-heads/mood_happy/mood_happy-discogs-effnet-1.pb
  mood_sad-discogs-effnet-1  reads effnet; TensorflowPredict2D(input="model/Placeholder", output="model/Softmax"); 2 outputs
    https://essentia.upf.edu/models/classification-heads/mood_sad/mood_sad-discogs-effnet-1.pb
  mood_relaxed-discogs-effnet-1  reads effnet; TensorflowPredict2D(input="model/Placeholder", output="model/Softmax"); 2 outputs
    https://essentia.upf.edu/models/classification-heads/mood_relaxed/mood_relaxed-discogs-effnet-1.pb
  mood_aggressive-discogs-effnet-1  reads effnet; TensorflowPredict2D(input="model/Placeholder", output="model/Softmax"); 2 outputs
    https://essentia.upf.edu/models/classification-heads/mood_aggressive/mood_aggressive-discogs-effnet-1.pb
  mood_party-discogs-effnet-1  reads effnet; TensorflowPredict2D(input="model/Placeholder", output="model/Softmax"); 2 outputs
    https://essentia.upf.edu/models/classification-heads/mood_party/mood_party-discogs-effnet-1.pb
  deam-msd-musicnn-2  reads musicnn; TensorflowPredict2D(input="model/Placeholder", output="model/Identity"); 2 outputs
    https://essentia.upf.edu/models/classification-heads/deam/deam-msd-musicnn-2.pb

averages.csv: one row per song, the mean over all patches of each output (column = "model: class").
per-patch/*.csv.gz: one row per song per patch (patch 0 = start of the clip), values as returned.

Models by the Music Technology Group, Universitat Pompeu Fabra (essentia.upf.edu/models), CC BY-NC-SA 4.0.

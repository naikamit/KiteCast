The Vosk speech model goes here, as a folder named `model-en-us`.

    curl -LO https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
    unzip vosk-model-small-en-us-0.15.zip
    mv vosk-model-small-en-us-0.15 model-en-us

You should end up with `app/src/main/assets/model-en-us/` containing `am/`,
`conf/`, `graph/`, `ivector/` and a couple of files. About 40 MB, and it is
not in git — it is a third-party binary and would bloat the repository.

Without it the app builds and runs but cannot hear anything; the log says the
model failed to unpack.

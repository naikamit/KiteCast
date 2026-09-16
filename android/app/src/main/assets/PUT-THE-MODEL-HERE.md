The Vosk speech model goes here, as a folder named `model-en-us`.

    curl -LO https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
    unzip vosk-model-small-en-us-0.15.zip
    mv vosk-model-small-en-us-0.15 model-en-us
    echo vosk-model-small-en-us-0.15 > model-en-us/uuid

That last line matters. Vosk's `StorageService` reads `<model>/uuid` to decide
whether the copy in internal storage is stale; the models in Vosk's own demo
carry one, the plain zip does not, and without it unpacking fails and the app
never gets a recogniser.

You should end up with `app/src/main/assets/model-en-us/` containing `am/`,
`conf/`, `graph/`, `ivector/`, `uuid` and a README. About 40 MB, and it is
not in git — it is a third-party binary and would bloat the repository.

Without it the app builds and runs but cannot hear anything; the log says the
model failed to unpack.

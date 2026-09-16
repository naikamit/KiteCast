plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.millsandgoon.ear"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.millsandgoon.ear"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"

        // Vosk ships libvosk.so for four architectures and a phone uses one.
        // The other three were 26.6 MB of an 82 MB APK. Every Android device
        // since roughly 2017 is arm64; add "armeabi-v7a" back here if an
        // older 32-bit device ever needs to run this.
        ndk { abiFilters += listOf("arm64-v8a") }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    buildFeatures { viewBinding = true }

    packaging { resources.excludes += "META-INF/*" }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.drawerlayout:drawerlayout:1.2.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.media:media:1.7.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    // Offline speech recognition with a constrained grammar — the whole reason
    // this app exists rather than the web one.
    implementation("com.alphacephei:vosk-android:0.3.47")
    implementation("net.java.dev.jna:jna:5.13.0@aar")

    // The lesson ladder and the page's decisions, checked on the JVM — no
    // device, no emulator, so CI runs them on every push.
    testImplementation("junit:junit:4.13.2")
}

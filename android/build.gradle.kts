// Versions kept conservative deliberately: this project is written without a
// compiler to hand, so the toolchain is a combination known to work together
// rather than the newest of each.
plugins {
    id("com.android.application") version "8.7.3" apply false
    id("org.jetbrains.kotlin.android") version "2.0.21" apply false
}

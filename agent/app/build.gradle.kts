plugins {
    id("com.android.application")
}

android {
    namespace = "com.apituner.agent"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.apituner.agent"
        minSdk = 23
        targetSdk = 34
        versionCode = 11
        versionName = "0.1.8"
    }

    signingConfigs {
        create("release") {
            val ks = System.getenv("KEYSTORE_FILE")
            if (ks != null) {
                // Resolve from the agent project root (agent/), not app/.
                storeFile = rootProject.file(ks)
                storePassword = System.getenv("KEYSTORE_PASSWORD")
                keyAlias = System.getenv("KEY_ALIAS")
                keyPassword = System.getenv("KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        getByName("release") {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            // Use the release signing config only when a keystore is provided.
            if (System.getenv("KEYSTORE_FILE") != null) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlin {
        compilerOptions {
            jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.18.0")
    implementation("androidx.appcompat:appcompat:1.7.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.11.0")

    // Embedded HTTP server
    implementation("org.nanohttpd:nanohttpd:2.3.1")

    // JSON
    implementation("com.google.code.gson:gson:2.14.0")
}

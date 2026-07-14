package com.apituner.agent.util

import android.content.Context
import android.util.Log
import com.apituner.agent.control.AppLauncher
import com.google.gson.Gson
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Fetches [LATEST_JSON_URL], downloads a newer Agent APK when available, and
 * opens the system installer. User confirmation on the TV is still required.
 */
class UpdateChecker(
    private val context: Context,
    private val appLauncher: AppLauncher = AppLauncher(context),
) {
    private val gson = Gson()
    private val tag = "UpdateChecker"

    data class LatestManifest(
        val versionName: String = "",
        val versionCode: Long = 0,
        val apkUrl: String = "",
        val apkName: String = "",
        val sha256: String = "",
    )

    sealed class CheckResult {
        data class UpToDate(val current: AgentVersionInfo, val latest: LatestManifest) : CheckResult()
        data class UpdateAvailable(val current: AgentVersionInfo, val latest: LatestManifest) : CheckResult()
        data class InstallStarted(val latest: LatestManifest) : CheckResult()
        data class Skipped(val reason: String) : CheckResult()
        data class Failed(val message: String) : CheckResult()
    }

    fun fetchLatest(): LatestManifest {
        val conn = (URL(LATEST_JSON_URL).openConnection() as HttpURLConnection).apply {
            connectTimeout = 15_000
            readTimeout = 15_000
            instanceFollowRedirects = true
            requestMethod = "GET"
            setRequestProperty("Accept", "application/json")
            setRequestProperty("User-Agent", "APITuner-Agent/${AgentVersion.current(context).versionName}")
        }
        try {
            val code = conn.responseCode
            if (code !in 200..299) {
                throw IllegalStateException("latest.json HTTP $code")
            }
            val body = conn.inputStream.bufferedReader().use { it.readText() }
            return gson.fromJson(body, LatestManifest::class.java)
                ?: throw IllegalStateException("empty latest.json")
        } finally {
            conn.disconnect()
        }
    }

    /** Check only; do not download. */
    fun check(): CheckResult {
        if (!inFlight.compareAndSet(false, true)) {
            return CheckResult.Skipped("update already in progress")
        }
        return try {
            val current = AgentVersion.current(context)
            val latest = fetchLatest()
            if (latest.versionCode <= 0 || latest.apkUrl.isBlank()) {
                CheckResult.Failed("invalid latest.json")
            } else if (signingVariantMismatch(current, latest)) {
                CheckResult.Skipped(
                    "signing variant mismatch (installed=${current.apkNameHint}, remote=${latest.apkName})"
                )
            } else if (latest.versionCode <= current.versionCode) {
                CheckResult.UpToDate(current, latest)
            } else {
                CheckResult.UpdateAvailable(current, latest)
            }
        } catch (e: Exception) {
            Log.w(tag, "check failed: ${e.message}")
            CheckResult.Failed(e.message ?: "check failed")
        } finally {
            inFlight.set(false)
        }
    }

    /**
     * Fetch latest.json; if newer, download APK and open the installer.
     * @param force when true, ignore the auto-check preference (manual button).
     */
    fun checkAndInstallIfNewer(force: Boolean = false): CheckResult {
        if (!force && !AgentPrefs.isAutoUpdateEnabled(context)) {
            return CheckResult.Skipped("auto-update disabled")
        }
        if (!inFlight.compareAndSet(false, true)) {
            return CheckResult.Skipped("update already in progress")
        }
        return try {
            val current = AgentVersion.current(context)
            val latest = fetchLatest()
            if (latest.versionCode <= 0 || latest.apkUrl.isBlank()) {
                return CheckResult.Failed("invalid latest.json")
            }
            if (signingVariantMismatch(current, latest)) {
                return CheckResult.Skipped(
                    "signing variant mismatch (installed=${current.apkNameHint}, remote=${latest.apkName})"
                )
            }
            if (latest.versionCode <= current.versionCode) {
                return CheckResult.UpToDate(current, latest)
            }
            val apk = downloadApk(latest)
            val ok = appLauncher.installApkFromFile(apk)
            if (ok) CheckResult.InstallStarted(latest)
            else CheckResult.Failed("failed to open install dialog")
        } catch (e: Exception) {
            Log.w(tag, "update failed: ${e.message}", e)
            CheckResult.Failed(e.message ?: "update failed")
        } finally {
            inFlight.set(false)
        }
    }

    private fun signingVariantMismatch(current: AgentVersionInfo, latest: LatestManifest): Boolean {
        val remoteDebug = latest.apkName.contains("-debug", ignoreCase = true)
        return current.isDebugBuild != remoteDebug
    }

    private fun downloadApk(latest: LatestManifest): File {
        val apkDir = File(context.cacheDir, "apk").apply { if (!exists()) mkdirs() }
        val dest = File(apkDir, "update_${latest.versionCode}.apk")
        if (dest.exists()) dest.delete()

        val conn = (URL(latest.apkUrl).openConnection() as HttpURLConnection).apply {
            connectTimeout = 30_000
            readTimeout = 120_000
            instanceFollowRedirects = true
            requestMethod = "GET"
            setRequestProperty("User-Agent", "APITuner-Agent/${AgentVersion.current(context).versionName}")
        }
        try {
            val code = conn.responseCode
            if (code !in 200..299) {
                throw IllegalStateException("APK download HTTP $code")
            }
            val digest = MessageDigest.getInstance("SHA-256")
            FileOutputStream(dest).use { out ->
                conn.inputStream.use { input ->
                    val buf = ByteArray(64 * 1024)
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        digest.update(buf, 0, n)
                    }
                }
            }
            val actual = digest.digest().joinToString("") { "%02x".format(it) }
            if (latest.sha256.isNotBlank() && !actual.equals(latest.sha256, ignoreCase = true)) {
                dest.delete()
                throw IllegalStateException("APK sha256 mismatch")
            }
            return dest
        } finally {
            conn.disconnect()
        }
    }

    companion object {
        const val LATEST_JSON_URL =
            "https://github.com/matthewfkoch/APITuner-releases/releases/latest/download/latest.json"

        private val inFlight = AtomicBoolean(false)

        /** Shared lock so service + UI don't race. */
        fun isBusy(): Boolean = inFlight.get()
    }
}

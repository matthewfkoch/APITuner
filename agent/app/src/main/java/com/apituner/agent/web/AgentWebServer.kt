/*
 * Derived from DisplayLauncher's LauncherWebServer (Apache-2.0):
 *   https://github.com/mouldybread/DisplayLauncher
 * Extended with stop / foreground / playback / key / info endpoints and
 * optional token auth for the APITuner Agent. See LICENSE and NOTICE.
 */
package com.apituner.agent.web

import android.content.Context
import android.os.Build
import android.util.Log
import com.apituner.agent.control.AppLauncher
import com.apituner.agent.control.ForegroundAppDetector
import com.apituner.agent.control.KeyAccessibilityService
import com.apituner.agent.control.PlaybackDetector
import com.apituner.agent.util.AgentPrefs
import com.google.gson.Gson
import com.google.gson.JsonObject
import fi.iki.elonen.NanoHTTPD
import java.io.File

class AgentWebServer(
    port: Int,
    private val context: Context,
    private val appLauncher: AppLauncher,
) : NanoHTTPD(port) {

    private val gson = Gson()
    private val tag = "AgentWebServer"
    private val foreground = ForegroundAppDetector(context)
    private val playback = PlaybackDetector(context)

    override fun serve(session: IHTTPSession): Response {
        return try {
            val uri = session.uri
            val method = session.method

            if (uri.startsWith("/api/") && !authorized(session)) {
                return json(mapOf("success" to false, "message" to "Unauthorized"), Response.Status.UNAUTHORIZED)
            }

            when {
                uri == "/" -> statusPage()
                uri == "/api/health" && method == Method.GET -> json(mapOf("success" to true, "message" to "APITuner Agent running"))
                uri == "/api/apps" && method == Method.GET -> getApps()
                uri == "/api/info" && method == Method.GET -> getInfo()
                uri == "/api/foreground" && method == Method.GET -> getForeground()
                uri == "/api/playback" && method == Method.GET -> getPlayback()
                uri == "/api/launch" && method == Method.POST -> launch(session)
                uri == "/api/launch-intent" && method == Method.POST -> launchIntent(session)
                uri == "/api/stop" && method == Method.POST -> handleStop()
                uri == "/api/key" && method == Method.POST -> key(session)
                uri == "/api/uninstall" && method == Method.POST -> uninstall(session)
                uri == "/api/upload-apk" && method == Method.POST -> uploadApk(session)
                else -> newFixedLengthResponse(Response.Status.NOT_FOUND, MIME_PLAINTEXT, "Not Found")
            }
        } catch (e: Exception) {
            Log.e(tag, "serve error: ${e.message}", e)
            json(mapOf("success" to false, "message" to "Server error: ${e.message}"))
        }
    }

    private fun authorized(session: IHTTPSession): Boolean {
        val token = AgentPrefs.getToken(context)
        if (token.isEmpty()) return true
        val provided = session.headers["x-auth-token"]
        return provided == token
    }

    private fun body(session: IHTTPSession): JsonObject {
        val map = HashMap<String, String>()
        session.parseBody(map)
        val data = map["postData"] ?: "{}"
        return try {
            gson.fromJson(data, JsonObject::class.java) ?: JsonObject()
        } catch (e: Exception) {
            JsonObject()
        }
    }

    private fun getApps(): Response = json(appLauncher.getInstalledApps())

    private fun getInfo(): Response {
        val caps = mapOf(
            "keys" to KeyAccessibilityService.isEnabled(),
            "current_app" to foreground.hasPermission(),
            "playback_state" to playback.hasPermission(),
            "power" to false,
            "app_list" to true,
            "install" to true,
        )
        return json(
            mapOf(
                "model" to Build.MODEL,
                "manufacturer" to Build.MANUFACTURER,
                "androidVersion" to Build.VERSION.RELEASE,
                "sdkInt" to Build.VERSION.SDK_INT,
                "packages" to appLauncher.getInstalledPackageNames(),
                "capabilities" to caps,
            )
        )
    }

    private fun getForeground(): Response = json(
        mapOf(
            "packageName" to foreground.currentForegroundPackage(),
            "hasPermission" to foreground.hasPermission(),
        )
    )

    private fun getPlayback(): Response {
        val (playing, pkg) = playback.playbackState()
        val result = HashMap<String, Any?>()
        if (playing != null) result["playing"] = playing
        result["package"] = pkg
        result["hasPermission"] = playback.hasPermission()
        return json(result)
    }

    private fun launch(session: IHTTPSession): Response {
        val pkg = body(session).get("packageName")?.asString
        if (pkg.isNullOrEmpty()) return json(mapOf("success" to false, "message" to "packageName required"))
        val ok = appLauncher.launchApp(pkg)
        return json(mapOf("success" to ok, "message" to if (ok) "launched" else "failed"))
    }

    private fun launchIntent(session: IHTTPSession): Response {
        val obj = body(session)
        val pkg = obj.get("packageName")?.asString
        if (pkg.isNullOrEmpty()) return json(mapOf("success" to false, "message" to "packageName required"))
        val action = obj.get("action")?.asString
        val data = obj.get("data")?.asString
        val component = obj.get("component")?.asString

        val extras = HashMap<String, String>()
        obj.get("extra_string")?.asString?.split(",")?.forEach { pair ->
            val parts = pair.split(":", limit = 2)
            if (parts.size == 2) extras[parts[0].trim()] = parts[1].trim()
        }
        obj.entrySet().forEach { (k, v) ->
            if (k.startsWith("extra_") && k != "extra_string") {
                extras[k.removePrefix("extra_")] = v.asString
            }
        }

        val ok = appLauncher.launchAppWithIntent(pkg, action, data, component, extras)
        return json(mapOf("success" to ok, "message" to if (ok) "launched" else "failed"))
    }

    private fun handleStop(): Response {
        val ok = appLauncher.goHome()
        return json(mapOf("success" to ok, "message" to "sent HOME"))
    }

    private fun key(session: IHTTPSession): Response {
        val key = body(session).get("key")?.asString
        if (key.isNullOrEmpty()) return json(mapOf("success" to false, "message" to "key required"))
        val svc = KeyAccessibilityService.instance
            ?: return json(mapOf("success" to false, "message" to "Accessibility service not enabled"))
        val ok = svc.sendKey(key)
        return json(mapOf("success" to ok, "message" to if (ok) "sent $key" else "unsupported key $key"))
    }

    private fun uninstall(session: IHTTPSession): Response {
        val pkg = body(session).get("packageName")?.asString
        if (pkg.isNullOrEmpty()) return json(mapOf("success" to false, "message" to "packageName required"))
        val ok = appLauncher.uninstallApp(pkg)
        return json(mapOf("success" to ok, "message" to if (ok) "uninstall dialog opened" else "failed"))
    }

    private fun uploadApk(session: IHTTPSession): Response {
        var apkFile: File? = null
        return try {
            val files = HashMap<String, String>()
            session.parseBody(files)
            val temp = files["file"] ?: return json(mapOf("success" to false, "message" to "No file uploaded"))
            val apkDir = File(context.cacheDir, "apk").apply { if (!exists()) mkdirs() }
            apkFile = File(apkDir, "uploaded_${System.currentTimeMillis()}.apk")
            File(temp).copyTo(apkFile!!, overwrite = true)
            File(temp).delete()
            val ok = appLauncher.installApkFromFile(apkFile!!)
            json(mapOf("success" to ok, "message" to if (ok) "install dialog opened" else "failed"))
        } catch (e: Exception) {
            apkFile?.delete()
            json(mapOf("success" to false, "message" to "Error: ${e.message}"))
        }
    }

    private fun statusPage(): Response {
        val html = """
            <!DOCTYPE html><html><head><meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>APITuner Agent</title>
            <style>body{font-family:sans-serif;background:#0e1117;color:#e6edf3;padding:24px}
            code{background:#1c2230;padding:2px 6px;border-radius:4px}</style></head>
            <body><h1>APITuner Agent</h1>
            <p>This device is controllable by APITuner over HTTP.</p>
            <p>Model: <code>${Build.MANUFACTURER} ${Build.MODEL}</code> · Android <code>${Build.VERSION.RELEASE}</code></p>
            <p>Endpoints under <code>/api/</code>. Add this device in APITuner using the <b>http_agent</b> backend.</p>
            </body></html>
        """.trimIndent()
        return newFixedLengthResponse(Response.Status.OK, "text/html", html)
    }

    private fun json(obj: Any?, status: Response.Status = Response.Status.OK): Response =
        newFixedLengthResponse(status, "application/json", gson.toJson(obj))
}

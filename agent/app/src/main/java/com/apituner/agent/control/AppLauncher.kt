/*
 * Derived from DisplayLauncher (Apache-2.0):
 *   https://github.com/mouldybread/DisplayLauncher
 * See LICENSE and NOTICE at the repository root.
 */
package com.apituner.agent.control

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.util.Log
import com.apituner.agent.InstallActivity
import com.apituner.agent.UninstallActivity
import java.io.File

data class AppInfo(val name: String, val packageName: String)

data class LaunchResult(val success: Boolean, val message: String)

class AppLauncher(val context: Context) {

    private val tag = "AppLauncher"

    fun getInstalledApps(): List<AppInfo> {
        val pm = context.packageManager
        // Include launchable system / preloaded apps (e.g. store-updated ESPN with
        // FLAG_SYSTEM). Filtering only FLAG_SYSTEM==0 hid those from Check packages
        // and the channel app picker while /api/info still listed the package.
        return pm.getInstalledApplications(PackageManager.GET_META_DATA)
            .mapNotNull { info ->
                try {
                    AppInfo(pm.getApplicationLabel(info).toString(), info.packageName)
                } catch (e: Exception) {
                    null
                }
            }
            .sortedBy { it.name.lowercase() }
    }

    fun getInstalledPackageNames(): List<String> =
        try {
            context.packageManager
                .getInstalledApplications(0)
                .map { it.packageName }
                .sorted()
        } catch (e: Exception) {
            emptyList()
        }

    fun launchApp(packageName: String): LaunchResult {
        val pm = context.packageManager
        if (!isPackageInstalled(packageName)) {
            return LaunchResult(false, "package not installed: $packageName")
        }
        return try {
            // Many Android TV / Fire apps (e.g. ESPN) expose only LEANBACK_LAUNCHER.
            // Prefer Leanback on TV devices; otherwise try phone LAUNCHER first.
            val leanbackFirst = pm.hasSystemFeature(PackageManager.FEATURE_LEANBACK)
            val intent = if (leanbackFirst) {
                leanbackLaunchIntent(packageName)
                    ?: pm.getLaunchIntentForPackage(packageName)
            } else {
                pm.getLaunchIntentForPackage(packageName)
                    ?: leanbackLaunchIntent(packageName)
            }
            if (intent == null) {
                Log.w(tag, "No LAUNCHER/LEANBACK_LAUNCHER activity for $packageName")
                return LaunchResult(
                    false,
                    "no LAUNCHER/LEANBACK_LAUNCHER activity for $packageName",
                )
            }
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            context.startActivity(intent)
            LaunchResult(true, "launched")
        } catch (e: Exception) {
            Log.e(tag, "launchApp failed: ${e.message}", e)
            LaunchResult(false, e.message ?: "launch failed")
        }
    }

    /** Resolve TV / Leanback launcher activity when the phone LAUNCHER is absent. */
    private fun leanbackLaunchIntent(packageName: String): Intent? {
        val probe = Intent(Intent.ACTION_MAIN).apply {
            addCategory(Intent.CATEGORY_LEANBACK_LAUNCHER)
            setPackage(packageName)
        }
        val resolve = context.packageManager.resolveActivity(probe, 0) ?: return null
        val info = resolve.activityInfo ?: return null
        return Intent(Intent.ACTION_MAIN).apply {
            addCategory(Intent.CATEGORY_LEANBACK_LAUNCHER)
            component = ComponentName(info.packageName, info.name)
        }
    }

    fun launchAppWithIntent(
        packageName: String,
        action: String?,
        data: String?,
        component: String?,
        extras: Map<String, String>?,
    ): LaunchResult {
        return try {
            val intent = Intent(action ?: Intent.ACTION_VIEW)
            if (!data.isNullOrEmpty()) {
                intent.data = Uri.parse(data)
            }
            if (!component.isNullOrEmpty()) {
                intent.component = resolveComponent(packageName, component)
            } else {
                intent.setPackage(packageName)
            }
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            extras?.forEach { (k, v) -> intent.putExtra(k, v) }

            // Fall back to the plain launch intent if the explicit intent can't resolve.
            if (intent.resolveActivity(context.packageManager) == null && component.isNullOrEmpty()) {
                return launchApp(packageName)
            }
            context.startActivity(intent)
            LaunchResult(true, "launched")
        } catch (e: Exception) {
            Log.e(tag, "launchAppWithIntent failed: ${e.message}", e)
            LaunchResult(false, e.message ?: "launch failed")
        }
    }

    /** Best-effort "stop": there is no non-root force-stop, so we go HOME. */
    fun goHome(): Boolean = try {
        val home = Intent(Intent.ACTION_MAIN).apply {
            addCategory(Intent.CATEGORY_HOME)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        context.startActivity(home)
        true
    } catch (e: Exception) {
        false
    }

    fun uninstallApp(packageName: String): Boolean = try {
        context.startActivity(
            Intent(context, UninstallActivity::class.java).apply {
                putExtra("packageName", packageName)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
        )
        true
    } catch (e: Exception) {
        false
    }

    fun installApkFromFile(apkFile: File): Boolean = try {
        context.startActivity(
            Intent(context, InstallActivity::class.java).apply {
                putExtra("apkPath", apkFile.absolutePath)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
        )
        true
    } catch (e: Exception) {
        try { apkFile.delete() } catch (_: Exception) {}
        false
    }

    private fun isPackageInstalled(packageName: String): Boolean = try {
        context.packageManager.getPackageInfo(packageName, 0)
        true
    } catch (_: PackageManager.NameNotFoundException) {
        false
    }

    private fun resolveComponent(packageName: String, component: String): ComponentName {
        return if (component.contains("/")) {
            val parts = component.split("/", limit = 2)
            ComponentName(parts[0], expandClass(parts[0], parts[1]))
        } else {
            ComponentName(packageName, expandClass(packageName, component))
        }
    }

    private fun expandClass(pkg: String, cls: String): String = when {
        cls.startsWith(".") -> pkg + cls
        !cls.contains(".") -> "$pkg.$cls"
        else -> cls
    }
}

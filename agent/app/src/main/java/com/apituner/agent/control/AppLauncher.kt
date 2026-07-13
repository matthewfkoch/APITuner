/*
 * Derived from DisplayLauncher (Apache-2.0):
 *   https://github.com/mouldybread/DisplayLauncher
 *   https://github.com/matthewfkoch/DisplayLauncher
 * See LICENSE and NOTICE at the repository root.
 */
package com.apituner.agent.control

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.net.Uri
import android.util.Log
import com.apituner.agent.InstallActivity
import com.apituner.agent.UninstallActivity
import java.io.File

data class AppInfo(val name: String, val packageName: String)

class AppLauncher(val context: Context) {

    private val tag = "AppLauncher"

    fun getInstalledApps(): List<AppInfo> {
        val pm = context.packageManager
        return pm.getInstalledApplications(PackageManager.GET_META_DATA)
            .filter { (it.flags and ApplicationInfo.FLAG_SYSTEM) == 0 }
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

    fun launchApp(packageName: String): Boolean = try {
        val intent = context.packageManager.getLaunchIntentForPackage(packageName)
        if (intent != null) {
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            context.startActivity(intent)
            true
        } else {
            false
        }
    } catch (e: Exception) {
        Log.e(tag, "launchApp failed: ${e.message}", e)
        false
    }

    fun launchAppWithIntent(
        packageName: String,
        action: String?,
        data: String?,
        component: String?,
        extras: Map<String, String>?,
    ): Boolean {
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
            true
        } catch (e: Exception) {
            Log.e(tag, "launchAppWithIntent failed: ${e.message}", e)
            false
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

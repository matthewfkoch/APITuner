package com.apituner.agent.util

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.widget.Toast

/**
 * Opens the correct system Settings screen for each Agent permission button.
 *
 * On Android 11+ (and especially Android 14 Google TV), [PackageManager.resolveActivity]
 * often returns null for Settings intents unless those actions are declared in
 * `<queries>`. We still try [Context.startActivity] when resolve fails — otherwise
 * buttons only show a Toast and never open Settings.
 *
 * Fire OS stubs many stock intents with CTSDummy* activities; those are skipped.
 * Fire overlay/usage/notification are handled in MainActivity (ADB setup dialog)
 * when still missing; after grant, these navigators are used for review.
 */
object SettingsNavigator {

    private const val TV_SETTINGS = "com.android.tv.settings"

    private val fireManufacturer: Boolean
        get() = Build.MANUFACTURER.equals("Amazon", ignoreCase = true) ||
            Build.MODEL.startsWith("AFT", ignoreCase = true)

    fun openOverlaySettings(context: Context) {
        val candidates = buildList {
            addAll(tvSettingsComponents(context, "device.apps.specialaccess.SystemAlertActivity"))
            add(
                Intent(
                    Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:${context.packageName}"),
                ),
            )
            add(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION))
            addAll(appDetailsFallbacks(context))
        }
        openFirstUseful(
            context,
            candidates,
            help = if (fireManufacturer) {
                "Fire TV: use APITuner dashboard → Grant permissions (ADB)"
            } else {
                "Settings → Apps → Special app access → Display over other apps → APITuner Agent"
            },
        )
    }

    fun openUsageAccessSettings(context: Context) {
        val candidates = buildList {
            addAll(tvSettingsComponents(context, "device.apps.specialaccess.AppUsageAccessActivity"))
            add(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
            addAll(appDetailsFallbacks(context))
        }
        openFirstUseful(
            context,
            candidates,
            help = if (fireManufacturer) {
                "Fire TV: use APITuner dashboard → Grant permissions (ADB)"
            } else {
                "Settings → Apps → Special app access → Usage access → APITuner Agent"
            },
        )
    }

    fun openNotificationListenerSettings(context: Context) {
        val candidates = buildList {
            addAll(tvSettingsComponents(context, "privacy.NotificationAccessActivity"))
            add(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
            addAll(appDetailsFallbacks(context))
        }
        openFirstUseful(
            context,
            candidates,
            help = if (fireManufacturer) {
                "Fire TV: use APITuner dashboard → Grant permissions (ADB)"
            } else {
                "Settings → Apps → Special app access → Notification access → APITuner Agent"
            },
        )
    }

    fun openAccessibilitySettings(context: Context) {
        val candidates = buildList {
            addAll(tvSettingsComponents(context, "oemlink.AccessibilitySettingsActivity"))
            add(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            addAll(appDetailsFallbacks(context))
        }
        openFirstUseful(
            context,
            candidates,
            help = if (fireManufacturer) {
                "Fire TV: Settings → Accessibility → APITuner Agent"
            } else {
                "Settings → System → Accessibility → APITuner Agent"
            },
        )
    }

    fun openHomeSettings(context: Context) {
        val candidates = buildList {
            add(Intent(Settings.ACTION_HOME_SETTINGS))
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                try {
                    val roleManager = context.getSystemService(android.app.role.RoleManager::class.java)
                    if (roleManager != null &&
                        roleManager.isRoleAvailable(android.app.role.RoleManager.ROLE_HOME)
                    ) {
                        add(roleManager.createRequestRoleIntent(android.app.role.RoleManager.ROLE_HOME))
                    }
                } catch (_: Exception) {
                    // fall through
                }
            }
            addAll(appDetailsFallbacks(context))
            add(Intent(Settings.ACTION_SETTINGS))
        }
        openFirstUseful(
            context,
            candidates,
            help = if (fireManufacturer) {
                "Fire TV: Settings → Applications → Default apps / Home (if available)"
            } else {
                "Settings → Apps → Default apps → Home app → APITuner Agent"
            },
        )
    }

    private fun tvSettingsComponents(context: Context, classSuffix: String): List<Intent> {
        if (!isPackageInstalled(context, TV_SETTINGS)) return emptyList()
        return listOf(
            Intent().setComponent(ComponentName(TV_SETTINGS, "$TV_SETTINGS.$classSuffix")),
        )
    }

    private fun appDetailsFallbacks(context: Context): List<Intent> = buildList {
        add(
            Intent(
                Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Uri.parse("package:${context.packageName}"),
            ),
        )
        addAll(
            tvSettingsComponents(context, "device.apps.AppManagementActivity").map {
                Intent(it).setData(Uri.parse("package:${context.packageName}"))
            },
        )
        add(Intent(Settings.ACTION_MANAGE_APPLICATIONS_SETTINGS))
        add(Intent(Settings.ACTION_APPLICATION_SETTINGS))
    }

    private fun openFirstUseful(context: Context, candidates: List<Intent>, help: String) {
        for (raw in candidates) {
            val intent = Intent(raw).addFlags(
                Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP,
            )
            // Only Settings actions expect CATEGORY_DEFAULT; role-request intents must not.
            if (intent.action?.startsWith("android.settings") == true &&
                !intent.hasCategory(Intent.CATEGORY_DEFAULT)
            ) {
                intent.addCategory(Intent.CATEGORY_DEFAULT)
            }
            if (isKnownUselessTarget(context, intent)) continue
            try {
                context.startActivity(intent)
                return
            } catch (_: Exception) {
                // try next candidate
            }
        }
        Toast.makeText(context, help, Toast.LENGTH_LONG).show()
    }

    /**
     * Skip Fire OS CTSDummy* stubs. When resolve returns null (common under
     * package visibility), allow the startActivity attempt — do not treat as useless.
     */
    private fun isKnownUselessTarget(context: Context, intent: Intent): Boolean {
        val resolved = try {
            context.packageManager.resolveActivity(intent, PackageManager.MATCH_DEFAULT_ONLY)
        } catch (_: Exception) {
            null
        } ?: return false
        val name = resolved.activityInfo?.name ?: return false
        return name.contains("CTSDummy", ignoreCase = true)
    }

    private fun isPackageInstalled(context: Context, packageName: String): Boolean {
        return try {
            context.packageManager.getPackageInfo(packageName, 0)
            true
        } catch (_: Exception) {
            false
        }
    }
}

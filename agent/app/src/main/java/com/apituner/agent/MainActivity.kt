package com.apituner.agent

import android.content.Intent
import android.graphics.Typeface
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.apituner.agent.control.ForegroundAppDetector
import com.apituner.agent.control.KeyAccessibilityService
import com.apituner.agent.control.PlaybackDetector
import com.apituner.agent.util.AgentPrefs
import java.net.NetworkInterface

class MainActivity : AppCompatActivity() {

    private lateinit var serverUrlView: TextView
    private lateinit var deviceInfoView: TextView
    private lateinit var tokenStatusView: TextView
    private lateinit var tokenInput: EditText
    private val permissionBadges = mutableMapOf<String, TextView>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AgentService.start(this)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(28), dp(24), dp(32))
            setBackgroundColor(color(R.color.bg))
        }

        root.addView(buildBrandHeader())
        root.addView(spacer(20))
        root.addView(buildConnectionCard())
        root.addView(spacer(16))
        root.addView(buildPermissionsCard())
        root.addView(spacer(16))
        root.addView(buildTokenCard())

        val scroll = ScrollView(this).apply {
            isFillViewport = true
            setBackgroundColor(color(R.color.bg))
            addView(root)
        }
        setContentView(scroll)
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
    }

    private fun buildBrandHeader(): LinearLayout {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }

        val logoWrap = LinearLayout(this).apply {
            gravity = Gravity.CENTER
            background = ContextCompat.getDrawable(this@MainActivity, R.drawable.bg_logo_mark)
            layoutParams = LinearLayout.LayoutParams(dp(44), dp(44)).apply {
                marginEnd = dp(14)
            }
        }
        logoWrap.addView(ImageView(this).apply {
            setImageResource(R.drawable.ic_logo_mark)
            layoutParams = LinearLayout.LayoutParams(dp(24), dp(24))
        })
        row.addView(logoWrap)

        val textCol = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        textCol.addView(TextView(this).apply {
            text = getString(R.string.brand_title)
            setTextColor(color(R.color.text))
            textSize = 22f
            setTypeface(typeface, Typeface.BOLD)
        })
        textCol.addView(TextView(this).apply {
            text = getString(R.string.brand_subtitle)
            setTextColor(color(R.color.text_muted))
            textSize = 12f
            setPadding(0, dp(2), 0, 0)
        })
        row.addView(textCol)
        return row
    }

    private fun buildConnectionCard(): LinearLayout {
        val card = card()
        card.addView(cardTitle(getString(R.string.connection_title)))
        card.addView(cardHint(getString(R.string.connection_hint)))

        serverUrlView = monoValue()
        card.addView(infoRow("Server URL", serverUrlView))

        deviceInfoView = monoValue()
        card.addView(infoRow("Device", deviceInfoView))

        tokenStatusView = monoValue()
        card.addView(infoRow("API token", tokenStatusView))
        return card
    }

    private fun buildPermissionsCard(): LinearLayout {
        val card = card()
        card.addView(cardTitle(getString(R.string.permissions_title)))
        card.addView(cardHint(getString(R.string.permissions_hint)))

        addPermission(
            card,
            key = "overlay",
            title = "Display over other apps",
            description = "Required so APITuner can launch streaming apps in the background.",
            required = true,
            granted = { Settings.canDrawOverlays(this) },
        ) {
            openOverlaySettings()
        }

        addPermission(
            card,
            key = "usage",
            title = "Usage Access",
            description = "Detects which app is in the foreground after a tune.",
            granted = { ForegroundAppDetector(this).hasPermission() },
        ) {
            open(Settings.ACTION_USAGE_ACCESS_SETTINGS)
        }

        addPermission(
            card,
            key = "notification",
            title = "Notification Access",
            description = "Reads media playback state to confirm a channel is playing.",
            granted = { PlaybackDetector(this).hasPermission() },
        ) {
            open(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)
        }

        addPermission(
            card,
            key = "accessibility",
            title = "Accessibility",
            description = "Optional. Sends BACK, HOME, and RECENTS key events.",
            granted = { KeyAccessibilityService.isEnabled() },
            optional = true,
        ) {
            open(Settings.ACTION_ACCESSIBILITY_SETTINGS)
        }

        addPermission(
            card,
            key = "home",
            title = "Default Home app",
            description = "Optional. Set APITuner Agent as the launcher for kiosk setups.",
            granted = { false },
            optional = true,
            showDivider = false,
        ) {
            open(Settings.ACTION_HOME_SETTINGS)
        }

        return card
    }

    private fun buildTokenCard(): LinearLayout {
        val card = card()
        card.addView(cardTitle(getString(R.string.token_title)))
        card.addView(cardHint(getString(R.string.token_hint)))

        tokenInput = EditText(this).apply {
            hint = getString(R.string.token_placeholder)
            setText(AgentPrefs.getToken(this@MainActivity))
            setTextColor(color(R.color.text))
            setHintTextColor(color(R.color.text_muted))
            setPadding(dp(14), dp(12), dp(14), dp(12))
            background = ContextCompat.getDrawable(this@MainActivity, R.drawable.bg_input)
            textSize = 14f
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = dp(12) }
        }
        card.addView(tokenInput)
        card.addView(primaryButton(getString(R.string.save_token)) {
            AgentPrefs.setToken(this, tokenInput.text.toString().trim())
            refreshStatus()
        })
        return card
    }

    private fun addPermission(
        card: LinearLayout,
        key: String,
        title: String,
        description: String,
        required: Boolean = false,
        optional: Boolean = false,
        showDivider: Boolean = true,
        granted: () -> Boolean,
        action: () -> Unit,
    ) {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, dp(14), 0, dp(14))
        }

        val head = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }

        val titleView = TextView(this).apply {
            text = title
            setTextColor(color(R.color.text))
            textSize = 15f
            setTypeface(typeface, Typeface.BOLD)
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        }
        head.addView(titleView)

        val badge = TextView(this).apply {
            textSize = 11f
            setTypeface(typeface, Typeface.BOLD)
            setPadding(dp(8), dp(4), dp(8), dp(4))
        }
        permissionBadges[key] = badge
        head.addView(badge)
        row.addView(head)

        row.addView(TextView(this).apply {
            text = description
            setTextColor(color(R.color.text_secondary))
            textSize = 13f
            setPadding(0, dp(4), 0, dp(10))
        })

        row.addView(secondaryButton(getString(R.string.grant), action))
        card.addView(row)
        if (showDivider) card.addView(divider())
    }

    private fun refreshStatus() {
        val ip = localIp()
        val port = AgentPrefs.DEFAULT_PORT
        serverUrlView.text = "http://$ip:$port"
        deviceInfoView.text = "${Build.MANUFACTURER} ${Build.MODEL} · Android ${Build.VERSION.RELEASE}"

        val token = AgentPrefs.getToken(this)
        tokenStatusView.text = if (token.isEmpty()) "Not set" else "Configured"

        updateBadge("overlay", Settings.canDrawOverlays(this), required = true)
        updateBadge("usage", ForegroundAppDetector(this).hasPermission())
        updateBadge("notification", PlaybackDetector(this).hasPermission())
        updateBadge("accessibility", KeyAccessibilityService.isEnabled(), optional = true)
        updateBadge("home", false, optional = true)
    }

    private fun updateBadge(key: String, granted: Boolean, required: Boolean = false, optional: Boolean = false) {
        val badge = permissionBadges[key] ?: return
        when {
            granted -> {
                badge.text = getString(R.string.badge_granted)
                badge.setTextColor(color(R.color.green))
                badge.background = ContextCompat.getDrawable(this, R.drawable.bg_badge_ok)
            }
            optional -> {
                badge.text = getString(R.string.badge_optional)
                badge.setTextColor(color(R.color.text_muted))
                badge.background = ContextCompat.getDrawable(this, R.drawable.bg_badge_warn)
            }
            required -> {
                badge.text = getString(R.string.badge_needed)
                badge.setTextColor(color(R.color.red))
                badge.background = ContextCompat.getDrawable(this, R.drawable.bg_badge_off)
            }
            else -> {
                badge.text = getString(R.string.badge_needed)
                badge.setTextColor(color(R.color.amber))
                badge.background = ContextCompat.getDrawable(this, R.drawable.bg_badge_warn)
            }
        }
    }

    private fun card(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        background = ContextCompat.getDrawable(this@MainActivity, R.drawable.bg_card)
        setPadding(dp(18), dp(18), dp(18), dp(18))
    }

    private fun cardTitle(text: String): TextView = TextView(this).apply {
        this.text = text
        setTextColor(color(R.color.text))
        textSize = 17f
        setTypeface(typeface, Typeface.BOLD)
    }

    private fun cardHint(text: String): TextView = TextView(this).apply {
        this.text = text
        setTextColor(color(R.color.text_secondary))
        textSize = 13f
        setPadding(0, dp(6), 0, 0)
    }

    private fun infoRow(label: String, valueView: TextView): LinearLayout {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, dp(12), 0, 0)
        }
        row.addView(TextView(this).apply {
            text = label
            setTextColor(color(R.color.text_muted))
            textSize = 13f
            layoutParams = LinearLayout.LayoutParams(dp(110), LinearLayout.LayoutParams.WRAP_CONTENT)
        })
        row.addView(valueView)
        return row
    }

    private fun monoValue(): TextView = TextView(this).apply {
        setTextColor(color(R.color.accent))
        textSize = 13f
        typeface = Typeface.MONOSPACE
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        )
    }

    private fun primaryButton(text: String, onClick: () -> Unit): Button = Button(this).apply {
        this.text = text
        isAllCaps = false
        setTextColor(color(R.color.accent_dark))
        setTypeface(typeface, Typeface.BOLD)
        background = ContextCompat.getDrawable(this@MainActivity, R.drawable.bg_button_primary)
        setPadding(dp(16), dp(12), dp(16), dp(12))
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { topMargin = dp(12) }
        setOnClickListener { onClick() }
        stateListAnimator = null
        elevation = 0f
    }

    private fun secondaryButton(text: String, onClick: () -> Unit): Button = Button(this).apply {
        this.text = text
        isAllCaps = false
        setTextColor(color(R.color.text))
        background = ContextCompat.getDrawable(this@MainActivity, R.drawable.bg_button_secondary)
        setPadding(dp(14), dp(10), dp(14), dp(10))
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        )
        setOnClickListener { onClick() }
        stateListAnimator = null
        elevation = 0f
    }

    private fun divider(): View = View(this).apply {
        setBackgroundColor(color(R.color.border))
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            dp(1),
        )
    }

    private fun spacer(heightDp: Int): View = View(this).apply {
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            dp(heightDp),
        )
    }

    private fun openOverlaySettings() {
        try {
            startActivity(
                Intent(
                    Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:$packageName"),
                ),
            )
        } catch (_: Exception) {
            open(Settings.ACTION_MANAGE_OVERLAY_PERMISSION)
        }
    }

    private fun open(action: String) {
        try {
            startActivity(Intent(action))
        } catch (_: Exception) {
            startActivity(Intent(Settings.ACTION_SETTINGS))
        }
    }

    private fun localIp(): String {
        try {
            for (intf in NetworkInterface.getNetworkInterfaces()) {
                for (addr in intf.inetAddresses) {
                    if (!addr.isLoopbackAddress && addr.hostAddress?.contains(":") == false) {
                        return addr.hostAddress ?: "0.0.0.0"
                    }
                }
            }
        } catch (_: Exception) {
        }
        return "0.0.0.0"
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private fun color(resId: Int): Int = ContextCompat.getColor(this, resId)
}

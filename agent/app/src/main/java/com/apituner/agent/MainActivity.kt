package com.apituner.agent

import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.apituner.agent.control.ForegroundAppDetector
import com.apituner.agent.control.KeyAccessibilityService
import com.apituner.agent.control.PlaybackDetector
import com.apituner.agent.util.AgentPrefs
import java.net.NetworkInterface

class MainActivity : AppCompatActivity() {

    private lateinit var statusView: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AgentService.start(this)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 48, 48, 48)
            setBackgroundColor(Color.parseColor("#0e1117"))
        }

        root.addView(title("APITuner Agent"))
        statusView = TextView(this).apply {
            setTextColor(Color.parseColor("#8b949e"))
            textSize = 14f
            setPadding(0, 8, 0, 24)
        }
        root.addView(statusView)

        root.addView(permButton("Grant Display over other apps (REQUIRED to launch apps)") {
            try {
                startActivity(
                    Intent(
                        Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                        Uri.parse("package:$packageName")
                    )
                )
            } catch (e: Exception) {
                open(Settings.ACTION_MANAGE_OVERLAY_PERMISSION)
            }
        })
        root.addView(permButton("Grant Usage Access (foreground app)") {
            open(Settings.ACTION_USAGE_ACCESS_SETTINGS)
        })
        root.addView(permButton("Grant Notification Access (playback state)") {
            open(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)
        })
        root.addView(permButton("Enable Accessibility (BACK/HOME/RECENTS keys)") {
            open(Settings.ACTION_ACCESSIBILITY_SETTINGS)
        })
        root.addView(permButton("Set default Home app (optional, kiosk)") {
            open(Settings.ACTION_HOME_SETTINGS)
        })

        val scroll = ScrollView(this).apply { addView(root) }
        setContentView(scroll)
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
    }

    private fun refreshStatus() {
        val overlay = Settings.canDrawOverlays(this)
        val fg = ForegroundAppDetector(this).hasPermission()
        val pb = PlaybackDetector(this).hasPermission()
        val keys = KeyAccessibilityService.isEnabled()
        statusView.text = buildString {
            appendLine("Control server: http://${localIp()}:${AgentPrefs.DEFAULT_PORT}")
            appendLine("Add this device in APITuner using the http_agent backend.")
            appendLine()
            appendLine("Capabilities:")
            appendLine("  ${check(overlay)} Launch apps (Display over other apps) - REQUIRED")
            appendLine("  ${check(true)} App list / install")
            appendLine("  ${check(fg)} Foreground app (Usage Access)")
            appendLine("  ${check(pb)} Playback state (Notification Access)")
            appendLine("  ${check(keys)} Keys BACK/HOME/RECENTS (Accessibility)")
        }
    }

    private fun check(ok: Boolean): String = if (ok) "[x]" else "[ ]"

    private fun title(text: String): TextView = TextView(this).apply {
        setText(text)
        setTextColor(Color.WHITE)
        textSize = 24f
        setPadding(0, 0, 0, 16)
    }

    private fun permButton(text: String, onClick: () -> Unit): Button = Button(this).apply {
        setText(text)
        setOnClickListener { onClick() }
        val lp = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT
        )
        lp.setMargins(0, 8, 0, 8)
        layoutParams = lp
    }

    private fun open(action: String) {
        try {
            startActivity(Intent(action))
        } catch (e: Exception) {
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
        } catch (_: Exception) {}
        return "0.0.0.0"
    }
}

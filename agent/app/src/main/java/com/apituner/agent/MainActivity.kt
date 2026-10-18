package com.apituner.agent

import android.app.AlertDialog
import android.app.role.RoleManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Typeface
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.Gravity
import android.view.ViewGroup
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
import com.apituner.agent.util.AgentVersion
import com.apituner.agent.util.SettingsNavigator
import com.apituner.agent.util.UpdateChecker
import java.net.NetworkInterface
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity() {

    private lateinit var pageHost: LinearLayout
    private lateinit var readinessView: TextView
    private lateinit var readinessHint: TextView
    private lateinit var nextActionButton: Button
    private lateinit var updateResultView: TextView
    private val pages = linkedMapOf<String, View>()
    private val navigation = linkedMapOf<String, Button>()
    private var currentPage = "Overview"
    private var returnFocus: View? = null
    private lateinit var serverUrlView: TextView
    private lateinit var deviceInfoView: TextView
    private lateinit var tokenStatusView: TextView
    private lateinit var versionView: TextView
    private lateinit var tokenInput: EditText
    private lateinit var autoUpdateButton: Button
    private lateinit var checkUpdateButton: Button
    private lateinit var editTokenButton: Button
    private lateinit var saveTokenButton: Button
    private lateinit var tokenEditor: LinearLayout
    private val permissionBadges = mutableMapOf<String, TextView>()
    private val permissionButtons = mutableMapOf<String, Button>()
    private val permissionGranted = mutableMapOf<String, () -> Boolean>()
    private val updateExecutor = Executors.newSingleThreadExecutor()
    private val isFireTv: Boolean =
        Build.MANUFACTURER.equals("Amazon", ignoreCase = true) ||
            Build.MODEL.startsWith("AFT", ignoreCase = true)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AgentService.start(this)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(32), dp(28), dp(32), dp(40))
            setBackgroundColor(color(R.color.bg))
            descendantFocusability = LinearLayout.FOCUS_AFTER_DESCENDANTS
        }

        root.addView(buildBrandHeader())
        root.addView(spacer(16))
        val nav = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        for (name in listOf("Overview", "Permissions", "Updates", "Advanced")) {
            val button = secondaryButton(name) { showPage(name) }.apply {
                id = View.generateViewId()
                layoutParams = LinearLayout.LayoutParams(0, dp(56), 1f).apply { marginEnd = dp(12) }
                contentDescription = name
            }
            navigation[name] = button
            nav.addView(button)
        }
        root.addView(nav)
        root.addView(spacer(20))
        pageHost = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f)
        }
        val overview = LinearLayout(this).apply {
            orientation = if (resources.configuration.screenWidthDp >= 720) LinearLayout.HORIZONTAL else LinearLayout.VERTICAL
        }
        val connection = buildConnectionCard()
        val readiness = buildReadinessCard()
        for (panel in listOf(connection, readiness)) {
            panel.layoutParams = if (overview.orientation == LinearLayout.HORIZONTAL) {
                LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply {
                    if (panel === connection) marginEnd = dp(20)
                }
            } else LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply { bottomMargin = dp(16) }
            overview.addView(panel)
        }
        pages["Overview"] = scrollPage(overview)
        pages["Permissions"] = scrollPage(buildPermissionsCard())
        pages["Updates"] = scrollPage(buildUpdatesCard())
        pages["Advanced"] = scrollPage(buildTokenCard())
        pages.values.forEach { pageHost.addView(it) }
        root.addView(pageHost)
        setContentView(root)
        showPage(savedInstanceState?.getString("page") ?: "Overview", moveFocus = false)
        root.post { navigation[currentPage]?.requestFocus() }
    }

    private fun scrollPage(content: View): ScrollView = ScrollView(this).apply {
        isFillViewport = true
        isFocusable = false
        descendantFocusability = ScrollView.FOCUS_AFTER_DESCENDANTS
        layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.MATCH_PARENT)
        addView(content)
    }

    private fun buildReadinessCard(): LinearLayout = card().apply {
        addView(cardTitle("Device setup"))
        readinessView = TextView(this@MainActivity).apply {
            textSize = 28f
            setTypeface(typeface, Typeface.BOLD)
            setPadding(0, dp(16), 0, dp(8))
        }
        addView(readinessView)
        readinessHint = cardHint("")
        addView(readinessHint)
        nextActionButton = primaryButton("Review permissions") {
            showPage("Permissions")
            pageHost.post { focusFirstAction() }
        }
        addView(nextActionButton)
        addView(cardHint("Device setup does not verify the HDMI encoder or DVR connection. Check those in the dashboard."))
    }

    private fun showPage(name: String, moveFocus: Boolean = true) {
        currentPage = if (pages.containsKey(name)) name else "Overview"
        pages.forEach { (key, page) -> page.visibility = if (key == currentPage) View.VISIBLE else View.GONE }
        navigation.forEach { (key, button) ->
            button.isSelected = key == currentPage
            button.contentDescription = if (key == currentPage) "$key, selected" else key
        }
        pageHost.post {
            wireFocusChain()
            if (moveFocus) {
                val first = visibleActions().firstOrNull()
                (first ?: navigation[currentPage])?.requestFocus()
            }
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putString("page", currentPage)
        super.onSaveInstanceState(outState)
    }

    override fun onPause() {
        returnFocus = currentFocus
        super.onPause()
    }

    @Deprecated("Uses Activity back handling for existing supported Android versions")
    override fun onBackPressed() {
        if (currentPage == "Advanced" && tokenEditor.visibility == View.VISIBLE) {
            tokenEditor.visibility = View.GONE
            editTokenButton.visibility = View.VISIBLE
            tokenInput.isFocusable = false
            tokenInput.isFocusableInTouchMode = false
            wireFocusChain()
            editTokenButton.requestFocus()
        } else if (currentPage != "Overview") {
            showPage("Overview", moveFocus = false)
            navigation["Overview"]?.requestFocus()
        } else super.onBackPressed()
    }

    override fun onDestroy() {
        updateExecutor.shutdownNow()
        super.onDestroy()
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
        pageHost.post {
            wireFocusChain()
            returnFocus?.takeIf { it.isShown && it.isFocusable }?.requestFocus()
            returnFocus = null
        }
    }

    private fun buildBrandHeader(): LinearLayout {
        val row = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        val textCol = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        textCol.addView(ImageView(this).apply {
            setImageResource(R.drawable.brand_wordmark)
            contentDescription = getString(R.string.brand_title)
            adjustViewBounds = true
            scaleType = ImageView.ScaleType.FIT_START
            layoutParams = LinearLayout.LayoutParams(dp(240), dp(69))
        })
        textCol.addView(TextView(this).apply {
            text = getString(R.string.brand_subtitle)
            setTextColor(color(R.color.text_muted))
            textSize = 18f
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
        card.addView(cardHint("Agent address"))
        serverUrlView.textSize = 22f
        serverUrlView.setPadding(0, dp(8), 0, dp(12))
        card.addView(serverUrlView)

        deviceInfoView = monoValue()
        card.addView(infoRow("Device", deviceInfoView))

        tokenStatusView = monoValue()
        card.addView(infoRow("API token", tokenStatusView))

        versionView = monoValue()
        card.addView(infoRow("Version", versionView))
        return card
    }

    private fun buildUpdatesCard(): LinearLayout {
        val card = card()
        card.addView(cardTitle(getString(R.string.updates_title)))
        card.addView(cardHint(getString(R.string.updates_hint)))

        autoUpdateButton = secondaryButton(autoUpdateLabel()) {
            val next = !AgentPrefs.isAutoUpdateEnabled(this)
            AgentPrefs.setAutoUpdateEnabled(this, next)
            autoUpdateButton.text = autoUpdateLabel()
        }
        card.addView(autoUpdateButton)

        checkUpdateButton = primaryButton(getString(R.string.check_for_updates)) {
            runManualUpdateCheck()
        }
        card.addView(checkUpdateButton)
        updateResultView = cardHint("Update results will appear here.").apply { accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE }
        card.addView(updateResultView)
        return card
    }

    private fun autoUpdateLabel(): String =
        if (AgentPrefs.isAutoUpdateEnabled(this)) {
            getString(R.string.auto_update_on)
        } else {
            getString(R.string.auto_update_off)
        }

    private fun runManualUpdateCheck() {
        checkUpdateButton.isEnabled = false
        checkUpdateButton.text = getString(R.string.update_checking)
        updateResultView.text = getString(R.string.update_checking)
        updateExecutor.execute {
            val result = try {
                UpdateChecker(this).checkAndInstallIfNewer(force = true)
            } catch (e: Exception) {
                UpdateChecker.CheckResult.Failed(e.message ?: "update failed")
            }
            AgentPrefs.setLastUpdateCheckMs(this, System.currentTimeMillis())
            runOnUiThread {
                checkUpdateButton.isEnabled = true
                checkUpdateButton.text = getString(R.string.check_for_updates)
                val message = when (result) {
                    is UpdateChecker.CheckResult.UpToDate ->
                        getString(R.string.update_up_to_date)
                    is UpdateChecker.CheckResult.UpdateAvailable ->
                        getString(R.string.update_available, result.latest.versionName)
                    is UpdateChecker.CheckResult.InstallStarted ->
                        getString(R.string.update_install_started)
                    is UpdateChecker.CheckResult.Skipped ->
                        getString(R.string.update_skipped, result.reason)
                    is UpdateChecker.CheckResult.Failed ->
                        getString(R.string.update_failed, result.message)
                }
                updateResultView.text = message
            }
        }
    }

    private fun buildPermissionsCard(): LinearLayout {
        val card = card()
        card.addView(cardTitle(getString(R.string.permissions_title)))
        card.addView(cardHint(if (isFireTv) "Fire TV: complete special permissions from this tuner in the dashboard using Grant permissions (ADB)." else getString(R.string.permissions_hint)))

        addPermission(
            card,
            key = "overlay",
            title = "Display over other apps",
            description = "Required so APITuner can launch streaming apps in the background.",
            required = true,
            granted = { Settings.canDrawOverlays(this) },
        ) {
            onPermissionAction("overlay") { SettingsNavigator.openOverlaySettings(this) }
        }

        addPermission(
            card,
            key = "usage",
            title = "Usage Access",
            description = "Detects which app is in the foreground after a tune.",
            granted = { ForegroundAppDetector(this).hasPermission() },
        ) {
            onPermissionAction("usage") { SettingsNavigator.openUsageAccessSettings(this) }
        }

        addPermission(
            card,
            key = "notification",
            title = "Notification Access",
            description = "Reads media playback state to confirm a channel is playing.",
            granted = { PlaybackDetector(this).hasPermission() },
        ) {
            onPermissionAction("notification") {
                SettingsNavigator.openNotificationListenerSettings(this)
            }
        }

        addPermission(
            card,
            key = "accessibility",
            title = "Accessibility",
            description = "Optional. Sends BACK, HOME, and RECENTS key events.",
            granted = { KeyAccessibilityService.isEnabled() },
            optional = true,
        ) {
            onPermissionAction("accessibility") {
                SettingsNavigator.openAccessibilitySettings(this)
            }
        }

        addPermission(
            card,
            key = "home",
            title = "Default Home app",
            description = "Optional. Set APITuner Agent as the launcher for kiosk setups.",
            granted = { isDefaultHomeApp() },
            optional = true,
            showDivider = false,
        ) {
            SettingsNavigator.openHomeSettings(this)
        }

        return card
    }

    private fun buildTokenCard(): LinearLayout {
        val card = card()
        card.addView(cardTitle(getString(R.string.token_title)))
        card.addView(cardHint(getString(R.string.token_hint)))

        // Keep the text field out of the D-pad path until the user chooses Edit.
        editTokenButton = secondaryButton(getString(R.string.edit_token)) {
            showTokenEditor()
        }
        card.addView(editTokenButton)

        tokenEditor = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
        }
        tokenInput = EditText(this).apply {
            hint = getString(R.string.token_placeholder)
            setText(AgentPrefs.getToken(this@MainActivity))
            setTextColor(color(R.color.text))
            setHintTextColor(color(R.color.text_muted))
            setPadding(dp(16), dp(14), dp(16), dp(14))
            background = ContextCompat.getDrawable(this@MainActivity, R.drawable.bg_input)
            textSize = 18f
            // TV remotes: don't steal focus until Edit is pressed.
            isFocusable = false
            isFocusableInTouchMode = false
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = dp(4) }
        }
        tokenEditor.addView(tokenInput)
        saveTokenButton = primaryButton(getString(R.string.save_token)) {
            AgentPrefs.setToken(this, tokenInput.text.toString().trim())
            tokenEditor.visibility = View.GONE
            editTokenButton.visibility = View.VISIBLE
            tokenInput.isFocusable = false
            tokenInput.isFocusableInTouchMode = false
            refreshStatus()
            wireFocusChain()
            editTokenButton.requestFocus()
        }
        tokenEditor.addView(saveTokenButton)
        card.addView(tokenEditor)
        return card
    }

    private fun showTokenEditor() {
        editTokenButton.visibility = View.GONE
        tokenEditor.visibility = View.VISIBLE
        tokenInput.isFocusable = true
        tokenInput.isFocusableInTouchMode = true
        wireFocusChain()
        tokenInput.requestFocus()
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
        permissionGranted[key] = granted

        val row = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, dp(12), 0, dp(12))
        }

        val head = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }

        val titleView = TextView(this).apply {
            text = "$title · ${if (required) "Required" else if (optional) "Optional" else "Recommended"}"
            setTextColor(color(R.color.text))
            textSize = 20f
            setTypeface(typeface, Typeface.BOLD)
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        }
        head.addView(titleView)

        val badge = TextView(this).apply {
            textSize = 18f
            setTypeface(typeface, Typeface.BOLD)
            setPadding(dp(10), dp(5), dp(10), dp(5))
        }
        permissionBadges[key] = badge
        head.addView(badge)
        row.addView(head)

        row.addView(TextView(this).apply {
            text = description
            setTextColor(color(R.color.text_secondary))
            textSize = 18f
            setPadding(0, dp(6), 0, dp(10))
        })

        val button = secondaryButton(getString(R.string.grant_needed), action).also {
            permissionButtons[key] = it
        }
        row.addView(button)
        card.addView(row)
        if (showDivider) card.addView(divider())
    }

    private fun refreshStatus() {
        val overlay = Settings.canDrawOverlays(this)
        val usage = ForegroundAppDetector(this).hasPermission()
        val notifications = PlaybackDetector(this).hasPermission()
        readinessView.text = when {
            !overlay -> "Setup required"
            !usage || !notifications -> "Playback checks limited"
            else -> "Permissions ready"
        }
        readinessView.setTextColor(color(if (!overlay) R.color.amber else if (!usage || !notifications) R.color.text else R.color.green))
        readinessHint.text = when {
            !overlay && isFireTv -> "On your computer, open this tuner in the dashboard and choose Grant permissions (ADB)."
            !overlay -> "Allow Display over other apps so the Agent can launch streaming apps."
            !usage || !notifications -> "Enable recommended permissions to improve foreground-app and playback detection."
            else -> "Required and recommended permissions are granted. Add this Agent address to your dashboard."
        }
        nextActionButton.text = if (!overlay || !usage || !notifications) "Complete setup" else "Review permissions"
        val ip = localIp()
        val port = AgentPrefs.DEFAULT_PORT
        serverUrlView.text = "http://$ip:$port"
        deviceInfoView.text = "${Build.MANUFACTURER} ${Build.MODEL} · Android ${Build.VERSION.RELEASE}"

        val token = AgentPrefs.getToken(this)
        tokenStatusView.text = if (token.isEmpty()) "Not set" else "Configured"

        val version = AgentVersion.current(this)
        versionView.text = "${version.versionName} (${version.versionCode})"
        if (::autoUpdateButton.isInitialized) {
            autoUpdateButton.text = autoUpdateLabel()
        }

        updateBadge("overlay", Settings.canDrawOverlays(this), required = true)
        updateBadge("usage", ForegroundAppDetector(this).hasPermission())
        updateBadge("notification", PlaybackDetector(this).hasPermission())
        updateBadge("accessibility", KeyAccessibilityService.isEnabled(), optional = true)
        updateBadge("home", isDefaultHomeApp(), optional = true)

        for ((key, button) in permissionButtons) {
            val ok = permissionGranted[key]?.invoke() == true
            button.text = when {
                ok -> getString(R.string.grant_review)
                isFireTv && key in fireHiddenPermissions -> getString(R.string.grant_fire_setup)
                else -> getString(R.string.grant_needed)
            }
        }
    }

    /** Fire OS does not expose these special-access toggles for sideloaded apps. */
    // Fire OS does not expose a usable grant UI for these; dashboard ADB only.
    private val fireHiddenPermissions =
        setOf("overlay", "usage", "notification", "accessibility")

    private fun isDefaultHomeApp(): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val roleManager = getSystemService(RoleManager::class.java)
            if (roleManager != null && roleManager.isRoleAvailable(RoleManager.ROLE_HOME)) {
                return roleManager.isRoleHeld(RoleManager.ROLE_HOME)
            }
        }
        val home = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME)
        val resolved = packageManager.resolveActivity(home, PackageManager.MATCH_DEFAULT_ONLY)
        return resolved?.activityInfo?.packageName == packageName
    }

    private fun onPermissionAction(key: String, openSettings: () -> Unit) {
        // Only show the ADB setup dialog when the permission is still missing.
        // After a successful grant, "Open settings" should open Settings (or app details).
        val granted = permissionGranted[key]?.invoke() == true
        if (isFireTv && key in fireHiddenPermissions && !granted) {
            showFirePermissionHelp(key)
            return
        }
        openSettings()
    }

    private fun showFirePermissionHelp(key: String) {
        val detail = when (key) {
            "overlay" -> "Display over other apps"
            "usage" -> "Usage Access"
            "notification" -> "Notification Access"
            "accessibility" -> "Accessibility (HOME/BACK keys)"
            else -> "This permission"
        }
        val message =
            "$detail cannot be granted from Fire TV Settings for sideloaded apps, " +
                "and the Agent cannot grant it itself.\n\n" +
                "One-time setup: on the APITuner dashboard open this tuner → " +
                "Grant permissions (ADB). That uses network ADB only for setup " +
                "(and may reboot once so Accessibility binds); " +
                "day-to-day tuning stays on the Agent HTTP API (no ADB).\n\n" +
                "On the Fire TV first enable Developer Options → ADB debugging and " +
                "accept the computer’s RSA prompt."
        AlertDialog.Builder(this)
            .setTitle(R.string.fire_grant_title)
            .setMessage(message)
            .setPositiveButton(android.R.string.ok, null)
            .show()
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
                badge.background = ContextCompat.getDrawable(this, R.drawable.bg_card)
            }
            required -> {
                badge.text = getString(R.string.badge_needed)
                badge.setTextColor(color(R.color.red))
                badge.background = ContextCompat.getDrawable(this, R.drawable.bg_badge_off)
            }
            else -> {
                badge.text = getString(R.string.badge_needed)
                badge.setTextColor(color(R.color.amber))
                badge.background = ContextCompat.getDrawable(this, R.drawable.bg_card)
            }
        }
    }

    private fun card(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        background = ContextCompat.getDrawable(this@MainActivity, R.drawable.bg_card)
        setPadding(dp(20), dp(20), dp(20), dp(20))
        // Cards themselves should not steal D-pad focus from buttons.
        isFocusable = false
        descendantFocusability = LinearLayout.FOCUS_AFTER_DESCENDANTS
    }

    private fun cardTitle(text: String): TextView = TextView(this).apply {
        this.text = text
        setTextColor(color(R.color.text))
        textSize = 24f
        setTypeface(typeface, Typeface.BOLD)
    }

    private fun cardHint(text: String): TextView = TextView(this).apply {
        this.text = text
        setTextColor(color(R.color.text_secondary))
        textSize = 18f
        setPadding(0, dp(6), 0, 0)
    }

    private fun infoRow(label: String, valueView: TextView): LinearLayout {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, dp(12), 0, 0)
            isFocusable = false
        }
        row.addView(TextView(this).apply {
            text = label
            setTextColor(color(R.color.text_muted))
            textSize = 18f
            layoutParams = LinearLayout.LayoutParams(dp(120), LinearLayout.LayoutParams.WRAP_CONTENT)
        })
        row.addView(valueView)
        return row
    }

    private fun monoValue(): TextView = TextView(this).apply {
        setTextColor(color(R.color.accent))
        textSize = 18f
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
        textSize = 18f
        background = ContextCompat.getDrawable(this@MainActivity, R.drawable.bg_button_primary)
        setPadding(dp(18), dp(16), dp(18), dp(16))
        minHeight = dp(56)
        isFocusable = true
        isFocusableInTouchMode = false
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
        textSize = 18f
        background = ContextCompat.getDrawable(this@MainActivity, R.drawable.bg_button_secondary)
        setPadding(dp(18), dp(16), dp(18), dp(16))
        minHeight = dp(56)
        isFocusable = true
        isFocusableInTouchMode = false
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { topMargin = dp(8) }
        setOnClickListener { onClick() }
        stateListAnimator = null
        elevation = 0f
    }

    private fun visibleActions(): List<View> {
        val result = mutableListOf<View>()
        fun visit(view: View) {
            if (view.visibility != View.VISIBLE) return
            if ((view is Button || view is EditText) && view.isFocusable && view.isEnabled) result += view
            if (view is ViewGroup) for (i in 0 until view.childCount) visit(view.getChildAt(i))
        }
        pages[currentPage]?.let { visit(it) }
        return result
    }

    private fun wireFocusChain() {
        val actions = visibleActions()
        val nav = navigation.values.toList()
        (actions + nav).forEach { if (it.id == View.NO_ID) it.id = View.generateViewId() }
        nav.forEachIndexed { i, button ->
            button.nextFocusLeftId = nav[maxOf(0, i - 1)].id
            button.nextFocusRightId = nav[minOf(nav.lastIndex, i + 1)].id
            button.nextFocusUpId = button.id
            button.nextFocusDownId = actions.firstOrNull()?.id ?: button.id
        }
        actions.forEachIndexed { i, view ->
            view.nextFocusUpId = if (i == 0) navigation[currentPage]!!.id else actions[i - 1].id
            view.nextFocusDownId = if (i == actions.lastIndex) view.id else actions[i + 1].id
            if (view !is EditText) {
                view.nextFocusLeftId = view.id
                view.nextFocusRightId = view.id
            }
        }
    }

    private fun focusFirstAction() {
        for (key in listOf("overlay", "usage", "notification")) {
            val button = permissionButtons[key] ?: continue
            if (permissionGranted[key]?.invoke() != true && button.isShown) {
                button.requestFocus()
                return
            }
        }
        visibleActions().firstOrNull()?.requestFocus()
    }

    private fun divider(): View = View(this).apply {
        setBackgroundColor(color(R.color.border))
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            dp(1),
        )
        isFocusable = false
    }

    private fun spacer(heightDp: Int): View = View(this).apply {
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            dp(heightDp),
        )
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

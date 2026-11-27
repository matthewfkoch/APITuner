package com.apituner.agent

import com.apituner.agent.control.AppLauncher
import com.apituner.agent.util.AgentVersionInfo
import com.apituner.agent.util.UpdateChecker
import com.apituner.agent.web.AgentWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class AgentLogicTest {

    @get:Rule
    val tempFolder = TemporaryFolder()

    @Test
    fun tokensMatch_emptyExpectedAllowsAnyRequest() {
        assertTrue(AgentWebServer.tokensMatch("", null))
        assertTrue(AgentWebServer.tokensMatch("", "anything"))
    }

    @Test
    fun tokensMatch_requiresExactSecretMatch() {
        assertTrue(AgentWebServer.tokensMatch("s3cr3t-token", "s3cr3t-token"))
        assertFalse(AgentWebServer.tokensMatch("s3cr3t-token", null))
        assertFalse(AgentWebServer.tokensMatch("s3cr3t-token", ""))
        assertFalse(AgentWebServer.tokensMatch("s3cr3t-token", "s3cr3t-toke"))
        assertFalse(AgentWebServer.tokensMatch("s3cr3t-token", "s3cr3t-token2"))
    }

    @Test
    fun pruneOldApks_deletesStaleApksButPreservesActiveAndOtherFiles() {
        val dir = tempFolder.newFolder("apk")
        val old1 = File(dir, "uploaded_1.apk").apply { writeText("old1") }
        val old2 = File(dir, "update_34.apk").apply { writeText("old2") }
        val active = File(dir, "uploaded_2.apk").apply { writeText("new") }
        val note = File(dir, "keep.txt").apply { writeText("meta") }

        AgentWebServer.pruneOldApks(dir, keep = active)

        assertFalse(old1.exists())
        assertFalse(old2.exists())
        assertTrue(active.exists())
        assertTrue(note.exists())
    }

    @Test
    fun escapeHtml_escapesSpecialCharacters() {
        assertEquals(
            "&lt;script&gt;&quot;a&quot; &amp; &#39;b&#39;&lt;/script&gt;",
            AgentWebServer.escapeHtml("<script>\"a\" & 'b'</script>"),
        )
        assertEquals("", AgentWebServer.escapeHtml(null))
    }

    @Test
    fun parseComponentParts_expandsRelativeBareAndExplicitComponents() {
        assertEquals(
            "com.fastchannels.player" to "com.fastchannels.player.PlaybackActivity",
            AppLauncher.parseComponentParts("com.other", "com.fastchannels.player/.PlaybackActivity"),
        )
        assertEquals(
            "com.example.tv" to "com.example.tv.MainActivity",
            AppLauncher.parseComponentParts("com.example.tv", ".MainActivity"),
        )
        assertEquals(
            "com.example.tv" to "com.example.tv.PlayerActivity",
            AppLauncher.parseComponentParts("com.example.tv", "PlayerActivity"),
        )
        assertEquals(
            "com.example.tv" to "org.vendor.CustomActivity",
            AppLauncher.parseComponentParts("com.example.tv", "org.vendor.CustomActivity"),
        )
    }

    @Test
    fun signingVariantMismatch_blocksDebugReleaseCrossInstall() {
        val release = AgentVersionInfo("0.1.36", 36, "release")
        val debug = AgentVersionInfo("0.1.36", 36, "debug")
        val remoteRelease = UpdateChecker.LatestManifest(apkName = "apituner-agent-0.1.37.apk")
        val remoteDebug = UpdateChecker.LatestManifest(apkName = "apituner-agent-0.1.37-debug.apk")

        assertFalse(UpdateChecker.isSigningVariantMismatch(release, remoteRelease))
        assertTrue(UpdateChecker.isSigningVariantMismatch(release, remoteDebug))
        assertTrue(UpdateChecker.isSigningVariantMismatch(debug, remoteRelease))
        assertFalse(UpdateChecker.isSigningVariantMismatch(debug, remoteDebug))
    }
}

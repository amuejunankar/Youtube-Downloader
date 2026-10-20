package com.amuejunankar.ytdl

import android.content.Context
import com.yausername.ffmpeg.FFmpeg
import com.yausername.youtubedl_android.YoutubeDL
import com.yausername.youtubedl_android.mapper.VideoInfo
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

data class EngineState(
    val ready: Boolean = false,
    val message: String = "",
)

object Engine {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val _state = MutableStateFlow(EngineState(false, "Starting…"))
    val state: StateFlow<EngineState> = _state.asStateFlow()

    private var started = false

    fun init(context: Context) {
        if (started) return
        started = true
        val app = context.applicationContext
        scope.launch {
            try {
                _state.value = EngineState(false, app.getString(R.string.engine_starting))
                YoutubeDL.getInstance().init(app)
                FFmpeg.getInstance().init(app)
                runCatching { YoutubeDL.getInstance().updateYoutubeDL(app) }
                _state.value = EngineState(true, app.getString(R.string.engine_ready))
            } catch (t: Throwable) {
                _state.value = EngineState(false, "Engine error: ${t.message}")
            }
        }
    }

    fun fetchInfo(url: String): VideoInfo = YoutubeDL.getInstance().getInfo(url)
}

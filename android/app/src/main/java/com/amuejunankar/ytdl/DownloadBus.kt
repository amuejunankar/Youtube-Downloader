package com.amuejunankar.ytdl

import kotlinx.coroutines.flow.MutableStateFlow

data class DownloadStatus(
    val running: Boolean = false,
    val progress: Float = -1f,
    val etaSeconds: Long = -1,
    val message: String = "",
    val finished: Boolean = false,
    val error: String? = null,
    val result: String? = null,
)

object DownloadBus {
    val status = MutableStateFlow(DownloadStatus())
}

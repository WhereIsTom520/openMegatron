import os
import customtkinter as ctk
import threading
import sounddevice as sd
import numpy as np
import time
import queue
import torch
import tkinter as tk
from funasr import AutoModel

cache_dir = os.environ.get("MODELSCOPE_CACHE") or os.path.join(os.getcwd(), ".models", "modelscope")
os.makedirs(cache_dir, exist_ok=True)
os.environ["MODELSCOPE_CACHE"] = cache_dir


class SpeechEngine:
    def __init__(self):
        self.device = "cuda:0" if torch.cuda.is_available() else "cpu"
        self.model = AutoModel(
            model="paraformer-zh",
            vad_model="fsmn-vad",
            punc_model="ct-punc",
            device=self.device,
            disable_update=True
        )
        self.sample_rate = 16000
        self.is_recording = False
        self.audio_queue = queue.Queue(maxsize=1000)

        dummy_audio = np.zeros(self.sample_rate, dtype="float32")
        self.model.generate(input=dummy_audio, batch_size_s=60)

        self.stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype='float32',
            blocksize=int(self.sample_rate * 0.1),
            callback=self._audio_callback
        )
        self.stream.start()

    def _audio_callback(self, indata, frames, time_info, status):
        if self.is_recording:
            try:
                self.audio_queue.put_nowait(indata.copy())
            except queue.Full:
                pass

    def start_recording(self):
        with self.audio_queue.mutex:
            self.audio_queue.queue.clear()
        self.is_recording = True

    def stop_and_transcribe(self):
        self.is_recording = False
        time.sleep(0.05)

        audio_chunks = []
        while not self.audio_queue.empty():
            audio_chunks.append(self.audio_queue.get())

        if not audio_chunks:
            return "", "未检测到有效语音输入"

        recording = np.concatenate(audio_chunks, axis=0).flatten()

        if len(recording) < self.sample_rate * 0.5:
            return "", "录音时间太短"

        try:
            res = self.model.generate(input=recording, batch_size_s=60)
            if res and len(res) > 0 and 'text' in res[0]:
                return res[0]['text'], None
            return "", None
        except Exception as e:
            return "", str(e)

    def close(self):
        if hasattr(self, 'stream') and self.stream.active:
            self.stream.stop()
            self.stream.close()


class GenericVoiceApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("智能语音识别引擎")
        self.geometry("800x600")
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.is_processing = False
        self._is_destroyed = False

        self.cmd_queue = queue.Queue()
        self.worker_thread = threading.Thread(target=self._model_worker_loop, daemon=True)
        self.worker_thread.start()

        self.setup_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.cmd_queue.put(("INIT", None))

    def setup_ui(self):
        self.text_output = ctk.CTkTextbox(self, font=("Arial", 16), wrap="word")
        self.text_output.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="nsew")
        self.text_output.insert("0.0", "欢迎使用本地离线语音识别。\n\n")
        self.text_output.configure(state="disabled")

        self.status_frame = ctk.CTkFrame(self, height=40, fg_color="transparent")
        self.status_frame.grid(row=1, column=0, padx=20, pady=5, sticky="ew")
        self.status_label = ctk.CTkLabel(self.status_frame, text="🟡 正在后台加载大模型，请稍候...",
                                         font=("Arial", 14, "bold"), text_color="orange")
        self.status_label.pack(side="left")

        self.record_btn = ctk.CTkButton(
            self, text="🎤 按住说话 / 松开识别",
            height=80, font=("Arial", 18, "bold"),
            state="disabled"
        )
        self.record_btn.grid(row=2, column=0, padx=20, pady=(10, 20), sticky="ew")

        self.record_btn.bind("<ButtonPress-1>", self.on_press_record)
        self.bind("<ButtonRelease-1>", self.on_release_record)

    def safe_ui_update(self, func, *args):
        if self._is_destroyed:
            return
        try:
            self.after(0, func, *args)
        except (tk.TclError, RuntimeError):
            pass

    def _model_worker_loop(self):
        engine = None
        while True:
            cmd, data = self.cmd_queue.get()

            if cmd == "INIT":
                try:
                    engine = SpeechEngine()
                    self.safe_ui_update(self.on_model_loaded, True, engine.device)
                except Exception as e:
                    self.safe_ui_update(self.on_model_loaded, False, str(e))

            elif cmd == "START_RECORD":
                if engine:
                    engine.start_recording()

            elif cmd == "STOP_TRANSCRIBE":
                if engine:
                    start_time = time.time()
                    transcribed_text, error = engine.stop_and_transcribe()
                    time_cost = time.time() - start_time
                    self.safe_ui_update(self.on_transcribe_done, transcribed_text, error, time_cost)

            elif cmd == "QUIT":
                if engine:
                    engine.close()
                break

    def on_model_loaded(self, success, info):
        if success:
            device_info = "GPU" if info.startswith("cuda") else "CPU"
            self.status_label.configure(text=f"🟢 模型加载完成 ({device_info})，系统就绪", text_color="green")
            self.record_btn.configure(state="normal")
            self.safe_append_text(f"模型已成功挂载至 {device_info}。")
        else:
            self.status_label.configure(text="🔴 模型加载失败", text_color="red")
            self.safe_append_text(f"[系统错误]: 模型加载失败 - {info}")

    def on_transcribe_done(self, transcribed_text, error, time_cost):
        if error:
            self.safe_append_text(f"[系统错误]: 识别失败 - {error}")
        elif not transcribed_text:
            self.safe_append_text("[系统提示]: 未检测到有效语音输入。")
        else:
            self.safe_append_text(f"🗣️ [转写结果] (耗时 {time_cost:.2f}s):\n{transcribed_text}\n")

        self.is_processing = False
        self.record_btn.configure(state="normal", text="🎤 按住说话 / 松开识别")
        self.status_label.configure(text="🟢 系统就绪", text_color="green")

    def safe_append_text(self, text):
        self.text_output.configure(state="normal")
        self.text_output.insert("end", text + "\n")
        self.text_output.see("end")
        self.text_output.configure(state="disabled")

    def on_press_record(self, event):
        if self._is_destroyed or self.record_btn.cget("state") == "disabled" or self.is_processing:
            return

        x, y = self.winfo_pointerx() - self.record_btn.winfo_rootx(), self.winfo_pointery() - self.record_btn.winfo_rooty()
        if not (0 <= x <= self.record_btn.winfo_width() and 0 <= y <= self.record_btn.winfo_height()):
            return

        self.is_processing = True
        self.status_label.configure(text="🔴 正在录音...", text_color="red")
        self.record_btn.configure(text="🎙️ 录音中... (松开结束)")

        self.cmd_queue.put(("START_RECORD", None))

    def on_release_record(self, event):
        if self._is_destroyed or self.record_btn.cget("state") == "disabled" or not self.is_processing:
            return

        self.record_btn.configure(state="disabled", text="⚙️ 极速识别中...")
        self.status_label.configure(text="🟡 正在进行文本转写...", text_color="orange")

        self.cmd_queue.put(("STOP_TRANSCRIBE", None))

    def on_closing(self):
        self._is_destroyed = True
        self.cmd_queue.put(("QUIT", None))
        self.destroy()


if __name__ == "__main__":
    app = GenericVoiceApp()
    app.mainloop()
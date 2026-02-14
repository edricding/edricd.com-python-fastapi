;(function (window) {
  "use strict";

  var DEFAULT_COLOR = "bg-primary";
  var DEFAULT_DURATION_MIN = 30;
  var SCHEDULE_API = "/api/reminder/schedule";
  var PRESET_LIST_API = "/api/reminder/preset/list";
  var PRESET_SAVE_API = "/api/reminder/preset/save";
  var PRESET_DELETE_API = "/api/reminder/preset/delete";

  function createApiError(message) {
    var err = new Error(message || "Request failed");
    err.isApiError = true;
    return err;
  }

  function requestJson(url, method, body) {
    var options = {
      method: method || "GET",
      credentials: "include",
      cache: "no-store",
      headers: {},
    };

    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }

    return fetch(url, options).then(function (res) {
      return res
        .json()
        .catch(function () {
          return {};
        })
        .then(function (data) {
          if (res.status === 401) {
            window.location.replace("/login");
            throw createApiError("Unauthorized");
          }
          if (!res.ok) {
            throw createApiError((data && (data.message || data.detail)) || ("HTTP " + String(res.status)));
          }
          return data || {};
        });
    });
  }

  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function toPositiveIntOrNull(value) {
    if (value === null || value === undefined || value === "") {
      return null;
    }
    var parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed < 1) {
      return null;
    }
    return Math.floor(parsed);
  }

  function toNonNegativeIntOrNull(value) {
    if (value === null || value === undefined || value === "") {
      return null;
    }
    var parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed < 0) {
      return null;
    }
    return Math.floor(parsed);
  }

  function normalizeColorClass(value) {
    if (typeof value !== "string") {
      return DEFAULT_COLOR;
    }
    var trimmed = value.trim();
    if (!trimmed) {
      return DEFAULT_COLOR;
    }
    if (!/^[a-zA-Z0-9_\-\s]+$/.test(trimmed)) {
      return DEFAULT_COLOR;
    }
    return trimmed;
  }

  function ReminderSettings() {
    this.listEl = document.getElementById("event-preset-list");
    this.btnAddPreset = document.getElementById("btn-add-preset");

    this.modalEl = document.getElementById("preset-modal");
    this.formEl = document.getElementById("preset-form");
    this.modalTitleEl = document.getElementById("preset-modal-title");

    this.presetIdEl = document.getElementById("preset-id");
    this.presetNameEl = document.getElementById("preset-name");
    this.presetDurationEl = document.getElementById("preset-duration");
    this.presetCategoryEl = document.getElementById("preset-category");
    this.presetAudioEl = document.getElementById("preset-audio");
    this.presetSortOrderEl = document.getElementById("preset-sort-order");
    this.presetEnabledEl = document.getElementById("preset-enabled");

    this.btnDeletePreset = document.getElementById("btn-delete-preset");

    this.modal = null;
    this.audios = [];
    this.presets = [];
    this.selectedPresetId = null;
  }

  ReminderSettings.prototype.init = function () {
    if (!this.listEl || !this.formEl || !this.modalEl || !window.bootstrap) {
      return;
    }

    this.modal = new bootstrap.Modal(this.modalEl, { backdrop: "static" });
    this.bindActions();
    this.loadData();
  };

  ReminderSettings.prototype.bindActions = function () {
    var self = this;

    if (this.btnAddPreset) {
      this.btnAddPreset.addEventListener("click", function () {
        self.openCreateModal();
      });
    }

    if (this.listEl) {
      this.listEl.addEventListener("click", function (event) {
        var trigger = event.target.closest(".js-preset-item");
        if (!trigger) {
          return;
        }
        var presetId = Number(trigger.getAttribute("data-preset-id"));
        if (!Number.isFinite(presetId) || presetId < 1) {
          return;
        }
        self.openEditModal(presetId);
      });
    }

    if (this.formEl) {
      this.formEl.addEventListener("submit", function (event) {
        event.preventDefault();
        self.savePreset();
      });
    }

    if (this.btnDeletePreset) {
      this.btnDeletePreset.addEventListener("click", function () {
        self.deletePreset();
      });
    }

    if (this.modalEl) {
      this.modalEl.addEventListener("hidden.bs.modal", function () {
        self.resetFormState();
      });
    }
  };

  ReminderSettings.prototype.loadData = function () {
    return Promise.allSettled([this.loadAudios(), this.loadPresets()]).then(function (results) {
      var firstFailure = null;
      for (var i = 0; i < results.length; i += 1) {
        var item = results[i];
        if (item && item.status === "rejected") {
          firstFailure = item.reason;
          break;
        }
      }

      if (!firstFailure) {
        return;
      }

      console.error("Load reminder settings failed", firstFailure);
      Swal.fire({
        title: "Load Failed",
        text: firstFailure && firstFailure.message ? firstFailure.message : "Failed to load reminder presets",
        icon: "error",
      });
    });
  };

  ReminderSettings.prototype.loadAudios = function () {
    var self = this;
    return requestJson(SCHEDULE_API, "GET").then(function (data) {
      var payload = data && data.data ? data.data : {};
      self.audios = Array.isArray(payload.audios) ? payload.audios : [];
      self.renderAudioOptions();
    });
  };

  ReminderSettings.prototype.loadPresets = function () {
    var self = this;
    return requestJson(PRESET_LIST_API, "GET").then(function (data) {
      if (!data || !data.success) {
        throw createApiError((data && data.message) || "Failed to load presets");
      }

      self.presets = Array.isArray(data.data) ? data.data : [];
      self.renderPresetList();
    });
  };

  ReminderSettings.prototype.renderAudioOptions = function () {
    if (!this.presetAudioEl) {
      return;
    }

    var previousValue = String(this.presetAudioEl.value || "");
    var html = '<option value="">None</option>';

    for (var i = 0; i < this.audios.length; i += 1) {
      var audio = this.audios[i];
      if (!audio || audio.id === undefined || audio.id === null) {
        continue;
      }
      var audioId = String(audio.id);
      var audioName = String(audio.name || ("Audio #" + audioId));
      html += '<option value="' + escapeHtml(audioId) + '">' + escapeHtml(audioName) + "</option>";
    }

    this.presetAudioEl.innerHTML = html;

    if (previousValue) {
      var matched = false;
      for (var j = 0; j < this.presetAudioEl.options.length; j += 1) {
        if (this.presetAudioEl.options[j].value === previousValue) {
          matched = true;
          break;
        }
      }
      this.presetAudioEl.value = matched ? previousValue : "";
    } else {
      this.presetAudioEl.value = "";
    }
  };

  ReminderSettings.prototype.renderPresetList = function () {
    if (!this.listEl) {
      return;
    }

    if (!this.presets.length) {
      this.listEl.innerHTML =
        '<div class="col-12"><div class="text-muted">No presets found. Click "Add Preset" to create one.</div></div>';
      return;
    }

    var html = "";
    for (var i = 0; i < this.presets.length; i += 1) {
      var preset = this.presets[i];
      var presetId = Number(preset && preset.id);
      if (!Number.isFinite(presetId) || presetId < 1) {
        continue;
      }

      var name = String(preset.name || "Untitled");
      var durationMin = Number(preset.duration_min || DEFAULT_DURATION_MIN);
      if (!Number.isFinite(durationMin) || durationMin < 1) {
        durationMin = DEFAULT_DURATION_MIN;
      }
      durationMin = Math.floor(durationMin);

      var colorClass = normalizeColorClass(preset.color || DEFAULT_COLOR);
      var sortOrder = Number(preset.sort_order || 0);
      var audioName =
        preset && preset.audio && preset.audio.name
          ? String(preset.audio.name)
          : (preset.audio_id ? "Audio #" + String(preset.audio_id) : "None");
      var enabledBadge =
        preset.is_enabled === false
          ? '<span class="badge bg-danger-subtle text-danger">Disabled</span>'
          : '<span class="badge bg-success-subtle text-success">Enabled</span>';

      html +=
        '<div class="col-12 col-md-6 col-xl-4">' +
        '<div class="card preset-card js-preset-item" data-preset-id="' +
        escapeHtml(String(presetId)) +
        '">' +
        '<div class="card-body">' +
        '<div class="d-flex justify-content-between align-items-start gap-2">' +
        '<h5 class="card-title mb-1 text-truncate">' +
        escapeHtml(name) +
        "</h5>" +
        enabledBadge +
        "</div>" +
        '<p class="text-muted mb-2">' +
        escapeHtml(String(durationMin) + " min") +
        "</p>" +
        '<p class="text-muted mb-1"><i class="ti ti-music me-1"></i>' +
        escapeHtml(audioName) +
        "</p>" +
        '<p class="text-muted mb-2"><i class="ti ti-sort-ascending me-1"></i>Sort: ' +
        escapeHtml(String(Number.isFinite(sortOrder) ? Math.floor(sortOrder) : 0)) +
        "</p>" +
        '<span class="badge badge-color-preview ' +
        escapeHtml(colorClass) +
        '">' +
        escapeHtml(colorClass) +
        "</span>" +
        "</div>" +
        "</div>" +
        "</div>";
    }

    this.listEl.innerHTML = html || '<div class="col-12"><div class="text-muted">No presets found.</div></div>';
  };

  ReminderSettings.prototype.findPresetById = function (presetId) {
    for (var i = 0; i < this.presets.length; i += 1) {
      var item = this.presets[i];
      if (Number(item && item.id) === Number(presetId)) {
        return item;
      }
    }
    return null;
  };

  ReminderSettings.prototype.resetFormState = function () {
    this.selectedPresetId = null;
    if (this.presetIdEl) {
      this.presetIdEl.value = "";
    }
    if (this.formEl) {
      this.formEl.classList.remove("was-validated");
    }
  };

  ReminderSettings.prototype.openCreateModal = function () {
    this.selectedPresetId = null;

    if (this.modalTitleEl) {
      this.modalTitleEl.textContent = "Add Preset";
    }
    if (this.presetIdEl) {
      this.presetIdEl.value = "";
    }
    if (this.presetNameEl) {
      this.presetNameEl.value = "";
    }
    if (this.presetDurationEl) {
      this.presetDurationEl.value = String(DEFAULT_DURATION_MIN);
    }
    if (this.presetCategoryEl) {
      this.presetCategoryEl.value = DEFAULT_COLOR;
    }
    if (this.presetAudioEl) {
      this.presetAudioEl.value = "";
    }
    if (this.presetSortOrderEl) {
      this.presetSortOrderEl.value = "";
    }
    if (this.presetEnabledEl) {
      this.presetEnabledEl.checked = true;
    }
    if (this.btnDeletePreset) {
      this.btnDeletePreset.style.display = "none";
    }

    if (this.formEl) {
      this.formEl.classList.remove("was-validated");
    }

    this.modal.show();
  };

  ReminderSettings.prototype.openEditModal = function (presetId) {
    var preset = this.findPresetById(presetId);
    if (!preset) {
      Swal.fire({
        title: "Data Missing",
        text: "Selected preset is no longer available. Refreshing list...",
        icon: "warning",
      });
      this.loadPresets();
      return;
    }

    this.selectedPresetId = Number(preset.id);

    if (this.modalTitleEl) {
      this.modalTitleEl.textContent = "Edit Preset";
    }
    if (this.presetIdEl) {
      this.presetIdEl.value = String(preset.id);
    }
    if (this.presetNameEl) {
      this.presetNameEl.value = String(preset.name || "");
    }
    if (this.presetDurationEl) {
      var duration = Number(preset.duration_min || DEFAULT_DURATION_MIN);
      if (!Number.isFinite(duration) || duration < 1) {
        duration = DEFAULT_DURATION_MIN;
      }
      this.presetDurationEl.value = String(Math.floor(duration));
    }
    if (this.presetCategoryEl) {
      this.presetCategoryEl.value = normalizeColorClass(preset.color || DEFAULT_COLOR);
    }
    if (this.presetAudioEl) {
      var audioId = toPositiveIntOrNull(preset.audio_id);
      this.presetAudioEl.value = audioId ? String(audioId) : "";
    }
    if (this.presetSortOrderEl) {
      var sortOrder = Number(preset.sort_order || 0);
      this.presetSortOrderEl.value = Number.isFinite(sortOrder) ? String(Math.floor(sortOrder)) : "0";
    }
    if (this.presetEnabledEl) {
      this.presetEnabledEl.checked = preset.is_enabled !== false;
    }
    if (this.btnDeletePreset) {
      this.btnDeletePreset.style.display = "inline-block";
    }

    if (this.formEl) {
      this.formEl.classList.remove("was-validated");
    }

    this.modal.show();
  };

  ReminderSettings.prototype.buildSavePayload = function () {
    if (!this.presetNameEl || !this.presetDurationEl || !this.presetCategoryEl) {
      throw createApiError("Form fields are missing");
    }

    var name = String(this.presetNameEl.value || "").trim();
    if (!name) {
      throw createApiError("Preset name is required");
    }

    var durationMin = Number(this.presetDurationEl.value || "");
    if (!Number.isFinite(durationMin) || durationMin < 1 || durationMin > 1439) {
      throw createApiError("Duration must be between 1 and 1439 minutes");
    }

    var payload = {
      name: name,
      duration_min: Math.floor(durationMin),
      color: normalizeColorClass(this.presetCategoryEl.value || DEFAULT_COLOR),
      audio_id: toPositiveIntOrNull(this.presetAudioEl ? this.presetAudioEl.value : ""),
      is_enabled: !!(this.presetEnabledEl && this.presetEnabledEl.checked),
      sort_order: toNonNegativeIntOrNull(this.presetSortOrderEl ? this.presetSortOrderEl.value : ""),
    };

    if (this.selectedPresetId) {
      payload.id = Number(this.selectedPresetId);
    }

    return payload;
  };

  ReminderSettings.prototype.savePreset = function () {
    var self = this;
    if (!this.formEl) {
      return;
    }

    if (!this.formEl.checkValidity()) {
      this.formEl.classList.add("was-validated");
      return;
    }

    var payload;
    try {
      payload = this.buildSavePayload();
    } catch (err) {
      Swal.fire({
        title: "Invalid Input",
        text: err && err.message ? err.message : "Please check your inputs",
        icon: "warning",
      });
      return;
    }

    requestJson(PRESET_SAVE_API, "POST", payload)
      .then(function (data) {
        if (!data || !data.success) {
          throw createApiError((data && data.message) || "Save failed");
        }

        self.modal.hide();
        return self.loadPresets();
      })
      .then(function () {
        Swal.fire({
          title: "Saved",
          text: "Preset saved successfully",
          icon: "success",
          timer: 1200,
          showConfirmButton: false,
        });
      })
      .catch(function (err) {
        console.error("Save reminder preset failed", err);
        Swal.fire({
          title: "Save Failed",
          text: err && err.message ? err.message : "Save failed",
          icon: "error",
        });
      });
  };

  ReminderSettings.prototype.deletePreset = function () {
    var self = this;
    if (!this.selectedPresetId) {
      return;
    }

    Swal.fire({
      title: "Delete Preset?",
      text: "This preset will be removed permanently.",
      icon: "warning",
      showCancelButton: true,
      confirmButtonText: "Delete",
      cancelButtonText: "Cancel",
    }).then(function (result) {
      if (!result || !result.isConfirmed) {
        return;
      }

      requestJson(PRESET_DELETE_API, "POST", { id: Number(self.selectedPresetId) })
        .then(function (data) {
          if (!data || !data.success) {
            throw createApiError((data && data.message) || "Delete failed");
          }

          self.modal.hide();
          return self.loadPresets();
        })
        .then(function () {
          Swal.fire({
            title: "Deleted",
            text: "Preset deleted",
            icon: "success",
            timer: 1200,
            showConfirmButton: false,
          });
        })
        .catch(function (err) {
          console.error("Delete reminder preset failed", err);
          Swal.fire({
            title: "Delete Failed",
            text: err && err.message ? err.message : "Delete failed",
            icon: "error",
          });
        });
    });
  };

  document.addEventListener("DOMContentLoaded", function () {
    new ReminderSettings().init();
  });
})(window);

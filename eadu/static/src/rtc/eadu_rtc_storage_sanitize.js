/** @odoo-module **/
// Part of Eadu. See LICENSE file for full copyright and licensing details.

const RTC_CAMERA_DEFAULT_PREFIX = "discuss_channel_camera_default_";

function isRtcCameraDefaultKey(key) {
    return typeof key === "string" && key.startsWith(RTC_CAMERA_DEFAULT_PREFIX);
}

function isValidJSON(value) {
    try {
        JSON.parse(value);
        return true;
    } catch {
        return false;
    }
}

function sanitizeRtcCameraDefault(key) {
    if (!isRtcCameraDefaultKey(key)) {
        return;
    }
    const value = localStorage.getItem(key);
    if (value !== null && !isValidJSON(value)) {
        localStorage.removeItem(key);
    }
}

for (const key of Object.keys(localStorage)) {
    sanitizeRtcCameraDefault(key);
}

const originalGetItem = Storage.prototype.getItem;
const originalSetItem = Storage.prototype.setItem;

Storage.prototype.getItem = function (key) {
    const value = originalGetItem.call(this, key);
    if (this === localStorage && isRtcCameraDefaultKey(key) && value !== null && !isValidJSON(value)) {
        this.removeItem(key);
        return null;
    }
    return value;
};

Storage.prototype.setItem = function (key, value) {
    if (this === localStorage && isRtcCameraDefaultKey(key) && !isValidJSON(value)) {
        this.removeItem(key);
        return;
    }
    return originalSetItem.call(this, key, value);
};

/** @odoo-module **/
// Part of Eadu. See LICENSE file for full copyright and licensing details.

import { PeerToPeer } from "@mail/discuss/call/common/peer_to_peer";
import { patch } from "@web/core/utils/patch";

const DEBUG_KEY = "eadu_rtc_debug";
const EADU_RTC_TRANSCEIVERS = [
    ["audio", "audio"],
    ["camera", "video"],
    ["screen", "video"],
];

function isDebugEnabled() {
    return window.localStorage?.getItem(DEBUG_KEY) !== "0";
}

function safeStringify(data) {
    try {
        return JSON.stringify(data);
    } catch {
        return "<unserializable>";
    }
}

function debug(message, data = {}) {
    if (!isDebugEnabled()) {
        return;
    }
    // Keep this as console.warn so it stands out from Odoo's normal RTC logs.
    console.warn(`[EADU RTC] ${message} ${safeStringify(data)}`);
}

function transceiverState(peer) {
    return (peer?.connection?.getTransceivers() || []).map((transceiver, index) => ({
        index,
        mid: transceiver.mid,
        direction: transceiver.direction,
        currentDirection: transceiver.currentDirection,
        stopped: transceiver.stopped,
        receiverTrack: trackState(transceiver.receiver?.track),
        senderTrack: trackState(transceiver.sender?.track),
    }));
}

function trackState(track) {
    if (!track) {
        return null;
    }
    return {
        id: track.id,
        kind: track.kind,
        enabled: track.enabled,
        muted: track.muted,
        readyState: track.readyState,
    };
}

function peerState(peer) {
    if (!peer) {
        return { missing: true };
    }
    return {
        id: peer.id,
        sequence: peer.sequence,
        hasPriority: peer.hasPriority,
        readyState: peer.ready?.state,
        signalingState: peer.connection?.signalingState,
        connectionState: peer.connection?.connectionState,
        iceConnectionState: peer.connection?.iceConnectionState,
        iceGatheringState: peer.connection?.iceGatheringState,
        dataChannelState: peer.dataChannel?.readyState,
        medias: Object.fromEntries(
            Object.entries(peer.medias || {}).map(([type, media]) => [
                type,
                {
                    accepted: media.accepted,
                    active: media.active,
                    track: trackState(media.track),
                },
            ])
        ),
        transceivers: transceiverState(peer),
    };
}

function rtcState(network) {
    return {
        selfId: network?.selfId,
        channelId: network?.channelId,
        localInfo: network?._localInfo,
        tracks: Object.fromEntries(
            Object.entries(network?._tracks || {}).map(([type, track]) => [type, trackState(track)])
        ),
        peers: Array.from(network?.peers?.values?.() || []).map((peer) => peerState(peer)),
    };
}

function exposeDebugDump(network) {
    window.__eaduRtcP2P = network;
    window.eaduRtcDump = () => {
        const state = rtcState(window.__eaduRtcP2P);
        console.warn(`[EADU RTC DUMP] ${safeStringify(state)}`);
        return state;
    };
}

function ensureStandardTransceivers(peer) {
    if (!peer?.connection) {
        return;
    }
    for (let index = peer.connection.getTransceivers().length; index < EADU_RTC_TRANSCEIVERS.length; index++) {
        const [streamType, mediaType] = EADU_RTC_TRANSCEIVERS[index];
        debug("adding missing transceiver before download update", {
            peerId: peer.id,
            index,
            streamType,
            mediaType,
            before: peerState(peer),
        });
        peer.connection.addTransceiver(mediaType);
    }
}

patch(PeerToPeer.prototype, {
    connect(selfId, channelId, options = {}) {
        exposeDebugDump(this);
        debug("connect", { selfId, channelId, info: options.info, iceServers: options.iceServers });
        return super.connect(selfId, channelId, options);
    },

    _createPeer(id, options = {}) {
        const peer = super._createPeer(id, options);
        debug("create peer", {
            selfId: this.selfId,
            peerId: id,
            options,
            peer: peerState(peer),
            rtc: rtcState(this),
        });
        peer.connection?.addEventListener("negotiationneeded", () => {
            debug("negotiationneeded", { selfId: this.selfId, peer: peerState(peer) });
        });
        peer.connection?.addEventListener("track", ({ transceiver, track }) => {
            debug("track received", {
                selfId: this.selfId,
                peerId: id,
                streamType: peer.getTransceiverStreamType(transceiver),
                track: trackState(track),
                peer: peerState(peer),
            });
        });
        peer.connection?.addEventListener("connectionstatechange", () => {
            debug("connection state", { selfId: this.selfId, peer: peerState(peer) });
        });
        peer.connection?.addEventListener("iceconnectionstatechange", () => {
            debug("ice connection state", { selfId: this.selfId, peer: peerState(peer) });
        });
        peer.dataChannel?.addEventListener("open", () => {
            debug("data channel open", { selfId: this.selfId, peer: peerState(peer) });
        });
        peer.dataChannel?.addEventListener("close", () => {
            debug("data channel close", { selfId: this.selfId, peer: peerState(peer) });
        });
        return peer;
    },

    updateDownload(id, states) {
        debug("update download requested", {
            selfId: this.selfId,
            peerId: id,
            states,
            before: peerState(this.peers.get(id)),
            rtc: rtcState(this),
        });
        ensureStandardTransceivers(this.peers.get(id));
        const result = super.updateDownload(id, states);
        debug("update download applied", {
            selfId: this.selfId,
            peerId: id,
            after: peerState(this.peers.get(id)),
            rtc: rtcState(this),
        });
        return result;
    },

    async updateUpload(streamType, track) {
        debug("update upload requested", {
            selfId: this.selfId,
            streamType,
            track: trackState(track),
            rtc: rtcState(this),
        });
        const result = await super.updateUpload(streamType, track);
        debug("update upload applied", {
            selfId: this.selfId,
            streamType,
            rtc: rtcState(this),
        });
        return result;
    },

    updateInfo(info) {
        debug("update info", { selfId: this.selfId, info });
        return super.updateInfo(info);
    },

    async handleNotification(id, content) {
        let parsed;
        try {
            parsed = JSON.parse(content);
        } catch {
            parsed = content;
        }
        if (
            parsed &&
            typeof parsed === "object" &&
            parsed.channelId !== this.channelId &&
            this.peers.has(id)
        ) {
            debug("normalizing data-channel notification channel", {
                selfId: this.selfId,
                peerId: id,
                event: parsed.event,
                remoteChannelId: parsed.channelId,
                localChannelId: this.channelId,
            });
            parsed = { ...parsed, channelId: this.channelId };
            content = safeStringify(parsed);
        }
        debug("incoming notification", {
            selfId: this.selfId,
            peerId: id,
            notification: parsed,
            before: peerState(this.peers.get(id)),
            rtc: rtcState(this),
        });
        const result = await super.handleNotification(id, content);
        debug("incoming notification handled", {
            selfId: this.selfId,
            peerId: id,
            event: parsed?.event,
            after: peerState(this.peers.get(id)),
            rtc: rtcState(this),
        });
        return result;
    },

    async _busNotify(event, options = {}) {
        debug("outgoing notification", {
            selfId: this.selfId,
            event,
            targets: options.targets,
            payload: options.payload,
            rtc: rtcState(this),
        });
        return super._busNotify(event, options);
    },

    async _updateRemote(peer, streamType) {
        debug("update remote requested", {
            selfId: this.selfId,
            streamType,
            track: trackState(this._tracks?.[streamType]),
            before: peerState(peer),
        });
        const result = await super._updateRemote(peer, streamType);
        debug("update remote applied", {
            selfId: this.selfId,
            streamType,
            after: peerState(peer),
        });
        return result;
    },

    _recover(id, reason = "") {
        debug("recover requested", {
            selfId: this.selfId,
            peerId: id,
            reason,
            peer: peerState(this.peers.get(id)),
        });
        return super._recover(id, reason);
    },
});

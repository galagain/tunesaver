// SPDX-License-Identifier: GPL-2.0-or-later
// CLI adapter for QM DSP. The beat detection algorithm is supplied by QMUL.
#include <dsp/onsets/DetectionFunction.h>
#include <dsp/tempotracking/TempoTrackV2.h>
#include <sndfile.h>
#include <algorithm>
#include <cmath>
#include <iomanip>
#include <iostream>
#include <vector>

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    SF_INFO info{};
    SNDFILE* file = sf_open(argv[1], SFM_READ, &info);
    if (!file || info.channels != 1 || info.samplerate != 22050) return 3;
    const int hop = 256, window = 512;
    DFConfig config{};
    config.DFType = DF_COMPLEXSD;
    config.stepSize = hop;
    config.frameLength = window;
    config.dbRise = 3;
    config.adaptiveWhitening = false;
    config.whiteningRelaxCoeff = -1;
    config.whiteningFloor = -1;
    DetectionFunction detector(config);
    std::vector<double> samples(window, 0), detection;
    while (sf_read_double(file, samples.data() + hop, hop) > 0) {
        detection.push_back(detector.processTimeDomain(samples.data()));
        std::copy(samples.begin() + hop, samples.end(), samples.begin());
        std::fill(samples.begin() + hop, samples.end(), 0);
    }
    sf_close(file);
    while (!detection.empty() && detection.back() <= 0) detection.pop_back();
    if (detection.size() < 8) { std::cout << "{\"beats\":[]}"; return 0; }
    detection.erase(detection.begin(), detection.begin() + 2);
    std::vector<double> period(detection.size()), tempi, beats;
    TempoTrackV2 tracker(info.samplerate, hop);
    tracker.calculateBeatPeriod(detection, period, tempi);
    tracker.calculateBeats(detection, period, beats);
    std::cout << std::setprecision(10) << "{\"beats\":[";
    for (size_t i = 0; i < beats.size(); ++i) {
        if (i) std::cout << ',';
        std::cout << (beats[i] * hop + hop / 2.0) / info.samplerate;
    }
    std::cout << "]}";
}

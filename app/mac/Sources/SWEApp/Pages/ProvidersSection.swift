import SWECore
import SwiftUI

/// Settings › Providers: four tabs. Harnesses are the CLIs that run
/// sessions; Inference is what they run on, the coding plans and the API
/// keys, in one list; Infra is the keys that deploy and manage
/// resources; Services is the agent-facing APIs — search, scraping and
/// browser services. The last three share the same `.api_keys` file.
struct ProvidersSection: View {
    enum Tab: String, CaseIterable {
        case harnesses, inference, infra, services

        var title: String {
            switch self {
            case .harnesses: "Harnesses"
            case .inference: "Inference"
            case .infra: "Infra"
            case .services: "Services"
            }
        }
    }

    @Environment(AppModel.self) private var app
    @AppStorage("providersTab") private var saved = Tab.harnesses.rawValue

    private var tab: Binding<Tab> {
        Binding(get: { Tab(rawValue: saved) ?? .harnesses }, set: { saved = $0.rawValue })
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Tabs(options: Tab.allCases.map { ($0, $0.title) }, selection: tab)
                .fixedSize()
            switch tab.wrappedValue {
            case .harnesses:
                ProviderVisibilityCard(model: app.providers)
            case .inference:
                InferenceSection()
            case .infra:
                KeysSectionView(section: .infra)
            case .services:
                KeysSectionView(section: .services)
            }
        }
    }
}

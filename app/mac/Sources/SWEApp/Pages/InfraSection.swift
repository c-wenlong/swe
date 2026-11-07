import AppKit
import SWECore
import SwiftUI

/// Settings › Providers › Infra: the keys that deploy and manage
/// resources rather than run inference — Vercel, Netlify, Cloudflare
/// and friends — in the same `.api_keys` file Inference edits, with the
/// same masking, reveal, ordering and checks. The catalogue rows are
/// `InfraList.items`: keys the infra providers claim by variable or
/// heading, then the services with no key yet. A name no catalogue
/// claims sits here only while a "mark" keeps it (a custom service the
/// user added or moved by hand).
struct InfraSection: View {
    /// `withKeys`, the default, hides every service with no key yet;
    /// "Any status" and the per-state picks show them.
    enum StateFilter: Hashable { case withKeys, all, state(InferenceKeyState) }

    @Environment(AppModel.self) private var app
    @State private var editing: KeyDraft?
    @State private var revealed: Set<String> = []
    @State private var actionError: String?
    @State private var stateFilter = StateFilter.withKeys
    @State private var cataloguing = false
    @State private var query = ""
    @State private var tableWidth: CGFloat = 0
    /// The provider whose heading is being renamed, and its draft name.
    @State private var renamingProvider = false
    @State private var providerBeingRenamed = ""
    @State private var providerDraft = ""
    /// The startup file a confirmed "Add to" would edit, nil while asked.
    @State private var confirmingSource: URL?
    @State private var sourceError: String?
    @State private var sourceAdded: String?

    static let firstWidth: CGFloat = 200
    static let compactFirstWidth: CGFloat = 150
    /// Below this table width the first column narrows so the value
    /// keeps its room.
    static let compactBelow: CGFloat = 560
    static let statusWidth: CGFloat = 120

    var body: some View {
        let model = app.apiKeys
        let marked = app.infraMarks.names
        let all = app.infraOrder.sorted(InfraList.items(keys: model.rows, marked: marked))
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .center, spacing: 8) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Infra keys").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                    Text(keysSubtitle(model)).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
                Spacer(minLength: 12)
                Button("Reload", systemImage: "arrow.clockwise") { actionError = nil; Task { await model.load() } }
                    .iconButton()
                    .disabled(model.isLoading)
                    .keyboardShortcut("r", modifiers: .command)
                    .help("Read the file and the shell again (⌘R)")
                Button("Check keys", systemImage: "checkmark.shield") { Task { await model.checkAll() } }
                    .secondaryButton()
                    .disabled(model.isChecking)
                    .hoverTip("Send each key to its own service to see if it still works", replacesLabel: false)
                Button("Catalogue", systemImage: "square.grid.2x2") { cataloguing = true }
                    .secondaryButton()
                    .hoverTip("Every infra service SWE knows; pick one to add its key", replacesLabel: false)
                Button("Add key", systemImage: "plus") {
                    editing = KeyDraft(provider: "", name: "", value: "", isNew: true)
                }
                .primaryButton()
            }
            if let error = model.error ?? actionError {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(Theme.bodySmall).foregroundStyle(Theme.warningText)
            }
            if let added = sourceAdded {
                Text(added).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            if let error = sourceError {
                Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
            }
            KeysFileNotices(model: model) { confirmingSource = $0 }
            filterBar(all)
            table(all)
            KeysFileFooter(model: model)
        }
        .task(id: model.path) { await model.load() }
        .onDisappear { revealed.removeAll() }
        .cardSheet(isPresented: $cataloguing) {
            ProviderCatalogueSheet(entries: InfraCatalogue.entries(keys: model.rows),
                                   title: "Infra catalogue",
                                   prompt: "Search infra services", kinds: []) { entry in
                if case .provider(let provider) = entry.target {
                    editing = KeyDraft(provider: "", name: "", value: "", isNew: true,
                                       providerID: provider.id)
                }
            }
        }
        .cardSheet(item: $editing) { draft in
            InfraKeyEditor(draft: draft, providers: model.providers, rows: model.rows,
                           save: { provider, name, value, tested in
                if draft.isNew {
                    try await model.set(name: name, value: value, provider: provider, tested: tested)
                } else {
                    try await model.update(name: draft.name, newName: name, provider: provider,
                                           value: value, tested: tested)
                    revealed.remove(draft.name)
                }
                settleMark(saved: name, previous: draft.isNew ? nil : draft.name)
            }, test: { name, value in
                await model.test(name: name, value: value)
            }, remove: { name in
                try await model.remove(name: name)
                revealed.remove(name)
            })
        }
        .alert("Rename provider", isPresented: $renamingProvider) {
            TextField("Name", text: $providerDraft)
            Button("Rename") {
                let from = providerBeingRenamed
                let to = providerDraft
                Task {
                    actionError = nil
                    do {
                        try await model.renameProvider(from: from, to: to)
                    } catch {
                        actionError = error.localizedDescription
                    }
                }
            }
            Button("Cancel", role: .cancel) {}
        }
        .keysSourceDialog(model: model, confirming: $confirmingSource,
                          added: $sourceAdded, error: $sourceError)
    }

    /// A key the catalogue claims stays wherever its own table puts it;
    /// anything else keeps an Infra mark so a row saved from this tab
    /// lands back here — and a rename away from a catalogue name gains
    /// the mark, so the key follows its edit rather than its old name.
    private func settleMark(saved name: String, previous: String?) {
        if let previous, previous != name { app.infraMarks.unmark(previous) }
        if APIKeyProviders.infraProvider(forVariable: name) == nil
            && APIKeyProviders.provider(forVariable: name) == nil {
            app.infraMarks.mark(name)
        } else {
            app.infraMarks.unmark(name)
        }
    }

    private func state(of item: InfraItem) -> InferenceKeyState {
        InferenceKeyState(hasKey: item.hasKey, check: item.key.flatMap { app.apiKeys.check(for: $0) }?.result)
    }

    private func visible(_ all: [InfraItem]) -> [InfraItem] {
        let state: InferenceKeyState? = if case .state(let value) = stateFilter { value } else { nil }
        return all.filter {
            (stateFilter != .withKeys || $0.hasKey)
                && InfraList.matches($0, state: state, query: query, stateOf: self.state(of:))
        }
    }

    /// The shared filter bar: the state select, then search taking the
    /// slack, then the count.
    private func filterBar(_ all: [InfraItem]) -> some View {
        func count(_ state: InferenceKeyState) -> Int { all.filter { self.state(of: $0) == state }.count }
        let shown = visible(all).count
        return HStack(spacing: 10) {
            SelectMenu(options: [(StateFilter.withKeys, "With keys (\(all.count - count(.noKey)))"),
                                 (.all, "Any status (\(all.count))"),
                                 (.state(.valid), "Valid (\(count(.valid)))"),
                                 (.state(.invalid), "Invalid (\(count(.invalid)))"),
                                 (.state(.unchecked), "Not checked (\(count(.unchecked)))"),
                                 (.state(.noKey), "No key (\(count(.noKey)))")],
                       selection: $stateFilter)
            SearchField(prompt: "Search services or variables", text: $query)
                .frame(maxWidth: .infinity)
            Text(shown == 1 ? "1 row" : "\(shown) rows")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary).monospacedDigit()
                .fixedSize()
        }
    }

    private func table(_ all: [InfraItem]) -> some View {
        let rows = visible(all)
        let compact = tableWidth > 0 && tableWidth < Self.compactBelow
        return VStack(spacing: 0) {
            HStack(spacing: 12) {
                Color.clear.frame(width: 12, height: 1)
                Text("Service").frame(width: compact ? Self.compactFirstWidth : Self.firstWidth,
                                      alignment: .leading)
                Text("Value").frame(maxWidth: .infinity, alignment: .leading)
                Text("Status").frame(width: Self.statusWidth, alignment: .leading)
            }
            .font(Theme.tableHeader).foregroundStyle(Theme.textSecondary)
            .padding(.horizontal, 16).padding(.vertical, 10)
            Divider().overlay(Theme.border)
            if rows.isEmpty {
                Text(emptyText(all))
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    .frame(maxWidth: .infinity).padding(.vertical, 20)
            } else {
                ReorderableRows(rows, showsDividers: true) { id, before in
                    app.infraOrder.move(id, before: before ?? after(rows, all: all, moving: id), in: all)
                } row: { item in
                    row(item, compact: compact)
                }
            }
        }
        .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { tableWidth = $0 }
    }

    /// What the table says when nothing shows.
    private func emptyText(_ all: [InfraItem]) -> String {
        if all.isEmpty {
            return "No infra keys yet. Add one and SWE writes it to \(displayPath(app.apiKeys.path))."
        }
        if stateFilter == .withKeys,
           all.contains(where: { !$0.hasKey && InfraList.matches($0, state: nil, query: query,
                                                                 stateOf: self.state(of:)) }) {
            return "Only services with no key match. Open the catalogue to add one, or pick “Any status” to see them here."
        }
        return "Nothing matches these filters."
    }

    /// Dropped below the last shown row: land just after it in the full
    /// order, not after every row the filters hide.
    private func after(_ rows: [InfraItem], all: [InfraItem], moving id: String) -> String? {
        guard let last = rows.last(where: { $0.id != id }),
              let index = all.firstIndex(where: { $0.id == last.id }) else { return nil }
        return all[(index + 1)...].first { $0.id != id }?.id
    }

    private func row(_ item: InfraItem, compact: Bool) -> some View {
        let model = app.apiKeys
        return InfraRow(
            item: item, compact: compact, isRevealed: revealed.contains(item.id),
            check: item.key.flatMap { model.check(for: $0) }, isChecking: model.checking.contains(item.id),
            recheck: { Task { await model.check(name: item.id) } },
            toggleReveal: {
                if revealed.contains(item.id) { revealed.remove(item.id) } else { revealed.insert(item.id) }
            },
            copy: { copySecret(item.key?.value ?? "") },
            open: { open(item) },
            renameProvider: (item.key?.provider.isEmpty ?? true) ? nil : {
                providerBeingRenamed = item.key?.provider ?? ""
                providerDraft = providerBeingRenamed
                renamingProvider = true
            },
            // A row with no catalogue service got here through a mark;
            // lifting the mark sends it back to Inference.
            moveToInference: item.provider == nil ? { app.infraMarks.unmark(item.id) } : nil)
    }

    private func open(_ item: InfraItem) {
        if let key = item.key {
            editing = KeyDraft(provider: key.provider, name: key.name, value: key.value, isNew: false)
        } else if let provider = item.provider {
            editing = KeyDraft(provider: "", name: "", value: "", isNew: true,
                               providerID: provider.id)
        }
    }
}

/// One row: grip, logo and the service or heading, the variable's value
/// with reveal and copy beside it, then the status. Anywhere but those
/// buttons and the status opens the key.
private struct InfraRow: View {
    var item: InfraItem
    /// Narrow table: the variable name goes under the service name.
    var compact = false
    var isRevealed: Bool
    var check: APIKeyCheck?
    var isChecking: Bool
    var recheck: () -> Void
    var toggleReveal: () -> Void
    var copy: () -> Void
    var open: () -> Void
    /// Set on a key under a heading of its own, to rename that heading.
    var renameProvider: (() -> Void)?
    /// Set on a marked custom row, to send it back to Inference.
    var moveToInference: (() -> Void)?
    @State private var hovering = false

    private var logo: String? {
        item.provider?.logo ?? item.key.flatMap { APIKeyProviderLogo.logoID(for: $0.provider) }
    }

    var body: some View {
        HStack(spacing: 12) {
            GripDots()
            HStack(spacing: 8) {
                Group {
                    if let logo { HarnessIcon(harness: logo, size: 16) } else { Color.clear }
                }
                .frame(width: 16, height: 16)
                VStack(alignment: .leading, spacing: 1) {
                    Text(item.title).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary).lineLimit(1)
                    if compact {
                        Text(item.id).font(Theme.caption).foregroundStyle(Theme.textMuted).lineLimit(1)
                    }
                }
            }
            .frame(width: compact ? InfraSection.compactFirstWidth : InfraSection.firstWidth,
                   alignment: .leading)
            // The variable name lives in the editor; the row shows the value.
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(valueText)
                        .font(Theme.font(size: 12, design: .monospaced))
                        .foregroundStyle(item.hasKey ? Theme.textSecondary : Theme.textMuted)
                        .lineLimit(1).truncationMode(.middle)
                    if item.hasKey {
                        smallButton(isRevealed ? "Hide" : "Reveal", isRevealed ? "eye.slash" : "eye", action: toggleReveal)
                        CopyButton(help: "Copy the value", iconSize: 10, copy: copy)
                            .foregroundStyle(Theme.textMuted)
                            .buttonStyle(.plain)
                            .frame(width: 16, height: 16)
                            .contentShape(.rect)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Group {
                if let key = item.key, item.hasKey {
                    StatusLabel(status: key.status, check: check, isChecking: isChecking, recheck: recheck)
                } else {
                    Label("No key", systemImage: "minus.circle")
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
            }
            .frame(width: InfraSection.statusWidth, alignment: .leading)
        }
        .padding(.horizontal, 16).padding(.vertical, 10)
        .background(hovering ? Theme.rowHover : .clear)
        .contentShape(.rect)
        .onTapGesture(perform: open)
        .onHover { hovering = $0 }
        .help(item.hasKey ? "Click to edit \(item.id)" : "Click to add a key for \(item.title)")
        .contextMenu {
            Button(item.hasKey ? "Edit…" : "Add key…", action: open)
            if let renameProvider { Button("Rename provider…", action: renameProvider) }
            if let moveToInference { Button("Move to Inference", action: moveToInference) }
        }
        .accessibilityElement(children: .contain)
        .accessibilityAction(named: item.hasKey ? "Edit" : "Add key", open)
    }

    private var valueText: String {
        guard let key = item.key else { return "No key" }
        if key.value.isEmpty { return "Empty" }
        return isRevealed ? key.value : key.masked
    }

    /// Small and quiet beside the value, so the key stays the thing you read.
    private func smallButton(_ title: String, _ symbol: String, action: @escaping () -> Void) -> some View {
        Button(title, systemImage: symbol, action: action)
            .labelStyle(.iconOnly)
            .font(Theme.font(size: 10))
            .foregroundStyle(Theme.textMuted)
            .buttonStyle(.plain)
            .frame(width: 16, height: 16)
            .contentShape(.rect)
            .help(isRevealed ? "Hide the value" : "Show the value")
    }
}

/// Add, change or remove one infra key. A new key first picks its
/// service from the infra catalogue: a known service fixes the variable
/// (a pick when it has several) and offers the page that hands out keys;
/// "Custom provider" takes a heading and a variable name only — there is
/// no endpoint to learn. On an existing key the provider heading and the
/// variable name edit as they do on Inference: a rename, a move and a new
/// value save together. Saving a non-empty value tests it first where a
/// probe exists; a rejection shows instead of saving until "Save anyway".
private struct InfraKeyEditor: View {
    @Environment(\.dismissSheet) private var dismiss
    @State var draft: KeyDraft
    /// The name the row had when the sheet opened; the draft's changes.
    private let originalName: String
    /// The original value, to tell whether a test is worth offering.
    private let originalValue: String
    var providers: [String]
    /// Every row in the file, for heading picks and "already set" notes.
    var rows: [APIKeysModel.Row]
    /// provider, name, value, the test verdict to keep.
    var save: (String, String, String, APIKeyCheckResult?) async throws -> Void
    /// name, value -> the service's verdict.
    var test: (String, String) async -> APIKeyCheckResult
    var remove: (String) async throws -> Void
    @State private var showValue = false
    @State private var confirmingRemove = false
    @State private var error: String?
    @State private var working = false
    /// The new-key pick: "" nothing yet, "custom" the free-name path,
    /// else an `APIKeyProvider.id` from `APIKeyProviders.infra`.
    @State private var providerChoice = ""
    /// The variable a multi-variable service saves under.
    @State private var envVarChoice = ""
    /// The catalogue sheet over this editor, from Browse.
    @State private var browsing = false
    /// The custom service's heading and variable.
    @State private var customName = ""
    @State private var customVar = ""
    /// The last verdict and the (name, value) it describes.
    @State private var testResult: APIKeyCheckResult?
    @State private var testedFor = (name: "", value: "")
    @State private var testing = false
    /// A rejected test showed; the next click saves regardless.
    @State private var saveAnyway = false
    /// The test-then-save in flight; closing the sheet cancels it, so a
    /// Cancel during the test never saves.
    @State private var saveTask: Task<Void, Never>?

    init(draft: KeyDraft, providers: [String], rows: [APIKeysModel.Row],
         save: @escaping (String, String, String, APIKeyCheckResult?) async throws -> Void,
         test: @escaping (String, String) async -> APIKeyCheckResult,
         remove: @escaping (String) async throws -> Void) {
        _draft = State(initialValue: draft)
        originalName = draft.name
        originalValue = draft.value
        self.providers = providers
        self.rows = rows
        self.save = save
        self.test = test
        self.remove = remove
        let prefill = draft.providerID.flatMap { APIKeyProviders.infraProvider(forID: $0) }
        _providerChoice = State(initialValue: prefill?.id ?? "")
        _envVarChoice = State(initialValue: prefill?.envVars[0] ?? "")
    }

    /// The picked service on a new key, nil for "custom" or none yet.
    private var picked: APIKeyProvider? {
        draft.isNew ? APIKeyProviders.infraProvider(forID: providerChoice) : nil
    }

    /// Whether the custom-provider fields show.
    private var isCustom: Bool { draft.isNew && providerChoice == "custom" }

    /// The variable the save writes.
    private var finalName: String {
        if isCustom { return customVar.trimmingCharacters(in: .whitespaces) }
        if let picked {
            return envVarChoice.isEmpty ? picked.envVars[0] : envVarChoice
        }
        return draft.name.trimmingCharacters(in: .whitespaces)
    }

    /// The heading the save writes under.
    private var finalProvider: String {
        if isCustom { return customName.trimmingCharacters(in: .whitespaces) }
        if let picked { return APIKeyProviders.heading(for: picked, in: rows) }
        return draft.provider.trimmingCharacters(in: .whitespaces)
    }

    /// The free-text provider and variable fields show on edits; a new
    /// key always takes a catalogue or custom name.
    private var freeForm: Bool { !draft.isNew }

    private var nameProblem: String? {
        let name = draft.name.trimmingCharacters(in: .whitespaces)
        if name.isEmpty { return "Enter a variable name, like VERCEL_TOKEN" }
        if name.range(of: #"^[A-Za-z_][A-Za-z0-9_]*$"#, options: .regularExpression) == nil {
            return "Use letters, digits and underscores, not starting with a digit"
        }
        return nil
    }

    /// What stops a custom name, or nil: it must read back as a heading.
    private var customNameProblem: String? {
        let name = customName.trimmingCharacters(in: .whitespaces)
        if name.isEmpty { return nil }
        guard APIKeysFile.isLabel(name) else {
            return "Use a short label: letters, digits, spaces and ( ) . , & / -"
        }
        return nil
    }

    /// What stops the custom variable, or nil.
    private var customVarProblem: String? {
        let name = customVar.trimmingCharacters(in: .whitespaces)
        if name.isEmpty { return nil }
        if name.range(of: #"^[A-Za-z_][A-Za-z0-9_]*$"#, options: .regularExpression) == nil {
            return "Use letters, digits and underscores, not starting with a digit"
        }
        return nil
    }

    /// The verdict still describes the current name and value.
    private var currentTest: APIKeyCheckResult? {
        testedFor == (finalName, draft.value) ? testResult : nil
    }

    /// A test is worth offering: a new non-empty value, or an edit that
    /// changed the value or the variable it answers to.
    private var canTest: Bool {
        guard !draft.value.isEmpty,
              draft.isNew || draft.value != originalValue || finalName != originalName
        else { return false }
        return true
    }

    /// Where "Get a key" points on an existing key whose variable the
    /// catalogue knows.
    private var editKeyURL: URL? {
        guard !draft.isNew else { return nil }
        return APIKeyProviders.infraProvider(forVariable: finalName)?.keyURL
    }

    private var saveDisabled: Bool {
        if working || testing { return true }
        if draft.isNew && providerChoice.isEmpty { return true }
        if isCustom {
            if customName.trimmingCharacters(in: .whitespaces).isEmpty
                || customVar.trimmingCharacters(in: .whitespaces).isEmpty
                || customNameProblem != nil || customVarProblem != nil { return true }
        }
        if freeForm && nameProblem != nil { return true }
        return false
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(draft.isNew ? "Add a key" : "Edit \(originalName)").font(Theme.sectionTitle).tracking(-0.5)
            Group {
                if draft.isNew { providerPicker }
                if isCustom { customFields }
                if freeForm { providerField }
                if let picked { knownProviderFields(picked) }
                if freeForm { variableField }
                valueField
            }
            .disabled(testing)
            if let error {
                Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
            }
            HStack {
                if draft.isNew {
                    Button("Browse…") { browsing = true }
                        .secondaryButton()
                        .help("Every infra service SWE knows")
                        .disabled(working || testing)
                } else if confirmingRemove {
                    Text("Delete \(originalName)?").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .lineLimit(1).truncationMode(.middle)
                    Button("Delete", role: .destructive) { run { try await remove(originalName) } }
                        .secondaryButton()
                    Button("Keep") { confirmingRemove = false }.secondaryButton()
                } else {
                    Button("Delete", role: .destructive) { confirmingRemove = true }
                        .secondaryButton()
                        .help("Delete \(originalName) from the keys file")
                }
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction).secondaryButton()
                Button(saveAnyway ? "Save anyway" : (draft.isNew ? "Add key" : "Save")) { saveTapped() }
                    .keyboardShortcut(.defaultAction)
                    .primaryButton()
                    .disabled(saveDisabled)
            }
        }
        .padding(24)
        .frame(width: 520)
        .background(Theme.panel)
        .onDisappear { saveTask?.cancel() }
        .cardSheet(isPresented: $browsing) {
            ProviderCatalogueSheet(entries: InfraCatalogue.entries(keys: rows),
                                   title: "Infra catalogue",
                                   prompt: "Search infra services", kinds: []) { entry in
                if case .provider(let provider) = entry.target { providerChoice = provider.id }
            }
        }
    }

    /// The catalogue picker a new key starts with: the infra services,
    /// then "Custom provider" for anything else.
    private var providerPicker: some View {
        FormField("Provider") {
            PopoverSelect(options: APIKeyProviders.infra.map { ($0.id, $0.name, $0.logo) }
                              + [("custom", "Custom provider", nil)],
                          selection: $providerChoice,
                          title: providerChoice.isEmpty ? "Choose a provider" : nil)
            .onChange(of: providerChoice) {
                envVarChoice = picked?.envVars[0] ?? ""
                saveAnyway = false
            }
        }
    }

    /// The custom service's own fields: the heading its group takes and
    /// the variable it saves as. No base URL: there is no endpoint to
    /// point at, only a value to keep.
    private var customFields: some View {
        Group {
            FormField("Provider name", hint: customNameHint, problem: customNameProblem != nil) {
                TextField("MyHosting", text: $customName)
                    .sweField()
                    .onChange(of: customName) { saveAnyway = false }
            }
            FormField("Variable", hint: customVarProblem, problem: customVarProblem != nil) {
                TextField("MYHOSTING_TOKEN", text: $customVar)
                    .sweField(monospaced: true)
                    .onChange(of: customVar) { saveAnyway = false }
            }
            if !finalName.isEmpty, rows.contains(where: { $0.name == finalName }) {
                Text("\(finalName) is already set; saving replaces it.")
                    .font(Theme.caption).foregroundStyle(Theme.warningText)
            }
        }
    }

    /// The derived hint under the name field: the heading as the file
    /// reads it, or the reason the name won't do.
    private var customNameHint: String {
        if let problem = customNameProblem { return problem }
        return "The heading in the file, like \"# MyHosting\"."
    }

    /// What a picked service fixes: the variable it saves as (a pick
    /// when it has several), where to get a key, and a note when that
    /// variable is already in the file.
    private func knownProviderFields(_ provider: APIKeyProvider) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                if provider.envVars.count > 1 {
                    Text("saved as").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    PopoverSelect(options: provider.envVars.map { ($0, $0, nil) },
                                  selection: $envVarChoice)
                        .onChange(of: envVarChoice) { saveAnyway = false }
                } else {
                    Text("saved as \(provider.envVars[0])")
                        .font(Theme.font(size: 12, design: .monospaced))
                        .foregroundStyle(Theme.textSecondary)
                }
                Spacer(minLength: 8)
                Link("Get a key", destination: provider.keyURL).font(Theme.caption)
            }
            if rows.contains(where: { $0.name == finalName }) {
                Text("\(finalName) is already set; saving replaces it.")
                    .font(Theme.caption).foregroundStyle(Theme.warningText)
            }
        }
    }

    /// The group the key files under: edit-only now, free text plus the
    /// existing groups so a key can move or go ungrouped.
    private var providerField: some View {
        FormField("Provider") {
            HStack(spacing: 8) {
                // Another group's name moves the key to the end of it.
                TextField("VERCEL", text: $draft.provider).sweField()
                if !providers.isEmpty {
                    // "" is a real choice (ungrouped keys); it still
                    // needs a label in the menu.
                    PopoverSelect(options: providers.map {
                                      ($0, $0.isEmpty ? "Ungrouped" : $0, APIKeyProviderLogo.logoID(for: $0))
                                  },
                                  selection: $draft.provider, title: "Existing")
                }
            }
            if let editKeyURL {
                Link("Get a key", destination: editKeyURL).font(Theme.caption)
            }
        }
    }

    private var variableField: some View {
        FormField("Variable") {
            TextField("VERCEL_TOKEN", text: $draft.name)
                .sweField(monospaced: true)
                .onChange(of: draft.name) { saveAnyway = false }
            if !draft.name.isEmpty, let problem = nameProblem {
                Text(problem).font(Theme.caption).foregroundStyle(Theme.dangerText)
            }
        }
    }

    private var valueField: some View {
        FormField("Value") {
            HStack(spacing: 8) {
                Group {
                    if showValue {
                        TextField("Paste the token", text: $draft.value)
                    } else {
                        SecureField("Paste the token", text: $draft.value)
                    }
                }
                .sweField(monospaced: true)
                .onChange(of: draft.value) { saveAnyway = false }
                Button(showValue ? "Hide" : "Show", systemImage: showValue ? "eye.slash" : "eye") { showValue.toggle() }
                    .iconButton()
                    .help(showValue ? "Hide the value" : "Show the value")
                if canTest {
                    Button(testing ? "Testing…" : "Test", action: testTapped)
                        .secondaryButton()
                        .disabled(testing || freeForm && nameProblem != nil)
                }
            }
            if let result = currentTest {
                Text(result.words)
                    .font(Theme.caption)
                    .foregroundStyle(testColor(result))
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func testColor(_ result: APIKeyCheckResult) -> Color {
        switch APIKeyCheckStyle(result) {
        case .success: Theme.success
        case .rejected: Theme.danger
        case .unchanged: Theme.textSecondary
        }
    }

    private func testTapped() {
        testing = true
        let name = finalName
        let value = draft.value
        Task {
            let result = await test(name, value)
            testing = false
            testResult = result
            testedFor = (name, value)
        }
    }

    /// Delete or any plain action: no test runs first.
    private func run(_ action: @escaping () async throws -> Void) {
        working = true
        Task {
            defer { working = false }
            do {
                try await action()
                dismiss()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }

    /// Test first, then save with the verdict, through the same
    /// `KeySaveFlow` the Inference editor runs: a rejection stops the
    /// save once and turns the button into "Save anyway".
    private func saveTapped() {
        working = true
        error = nil
        let name = finalName
        let provider = finalProvider
        let value = draft.value
        let testable = canTest
        let flow = KeySaveFlow(
            test: {
                testing = true
                defer { testing = false }
                return await test(name, value)
            },
            save: { verdict in
                try await save(provider, name, value, verdict)
            })
        let current = currentTest
        let anyway = saveAnyway
        saveTask = Task {
            defer { working = false }
            let outcome = await flow.run(value: value, testable: testable, current: current,
                                         saveAnyway: anyway) { result in
                testResult = result
                testedFor = (name, value)
            }
            switch outcome {
            case .saved: dismiss()
            case .confirmAnyway: saveAnyway = true
            case .failed(let message): self.error = message
            case .cancelled: break
            }
        }
    }
}
